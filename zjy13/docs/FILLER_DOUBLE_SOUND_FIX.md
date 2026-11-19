# 填充音连发缺陷修复（板子会连着发出两声语气词）

日期：2026-11-19
现象：板子上会连着听到两三声「嗯…」「这个嘛…」，听起来不自然。

## 一、结论

**根因不是资源本身，而是发送节奏。**

`_send_fillers` 的注释写着"边播边补"，但实现上从来没有跟随播放进度 ——
它其实是**一口气把最多 3 秒填充音全塞给板子**。

修了两处：

1. `ws/server.py::_send_fillers` —— 发完一个就等它播完（可被正文打断）
2. `tts/fillers.py::MAX_FILLER_CLIPS` —— 新增声数上限，默认 **1 声**

实测：修复前每轮 **4~5 声**（日志可查），修复后 **1 声**。

## 二、根因分析

### 2.1 旧代码的实际行为

```python
while session.should_continue():      # 只看累计时长 < 3.0s
    clip = session.next_clip()
    for off in range(0, len(clip.pcm), step):
        await self.send_audio(client, clip.pcm[off:off+step], seq)  # 无节流
    count += 1
```

两个事实叠加导致问题：

| 事实 | 后果 |
|---|---|
| `should_continue()` 只看累计时长，**不看是否已播完** | 发完第 1 声（0.70s）后条件仍成立，立刻发第 2 声 |
| `send_audio` 对填充音**没有节流** | 一个 0.7s 的 clip = 9 帧 × 4096B，几毫秒内全部写进 socket |

于是服务端"认为"已经发了 3 秒，但这 3 秒音频是**瞬间**塞进去的，
板子要花 3 秒才播完。设计意图的"边播边补"在时序上从未成立。

### 2.2 对比：正文是有节流的

正文那边（`server.py:1399-1413`）按播放进度节流：

```python
played_sec = time.perf_counter() - play_start
sent_sec = (sent_bytes + len(piece)) / bytes_per_sec
ahead = sent_sec - played_sec
if ahead > lead_sec:
    await asyncio.sleep(ahead - lead_sec)
```

填充音路径**漏掉了这段**，是本次缺陷的直接原因。

### 2.3 实测证据

修复前的日志（`logs/ws_dbg_out.log`、`logs/ws_filler_debug.log`）：

```
正文接入填充音会话 turn=...-t76163 填充音=4 个 填充字节=137134 接续帧号=37
正文接入填充音会话 turn=...-t92465 填充音=4 个 填充字节=141160 接续帧号=38
正文接入填充音会话 turn=...-t89333 填充音=5 个 填充字节=174264 接续帧号=47
正文接入填充音会话 turn=...-t13973 填充音=5 个 填充字节=174956 接续帧号=47
```

每轮 4~5 个，累计约 2.7~3.5 秒 —— 用户听到的就是一串语气词。

## 三、修复方案

### 3.1 让发送节奏跟随播放进度

```python
count += 1
logger.debug(...)
# 关键：等这一声播完再考虑下一声
if session.should_continue():
    await self._wait_filler_played(
        clip.duration_s, body_ready=body_ready, cancel=cancel
    )
```

`_wait_filler_played` 用 `asyncio.wait` 而不是 `sleep`：

```python
waiters = [
    asyncio.create_task(body_ready.wait()),
    asyncio.create_task(cancel.wait()),
]
await asyncio.wait(waiters, timeout=duration_s,
                   return_when=asyncio.FIRST_COMPLETED)
```

**为什么不用 `asyncio.sleep(duration_s)`**：正文可能中途到达，必须能立刻
打断等待。否则首字延迟会被人为拉长最多一个填充音的时长。

未完成的任务必须 `cancel()` + `gather(return_exceptions=True)`，
否则 asyncio 会报 "Task was destroyed but it is pending"。

### 3.2 加声数上限兜底

```python
MAX_FILLER_CLIPS = 1   # 单轮最多发几声
```

`should_continue()` 里放在时长判断**之前**：

```python
if self._count == 0:
    return True          # 第一个总是发
if self._count >= self._max_clips:
    return False         # 声数上限先拦
typical = max(c.duration_s for c in self._bank.clips)
return self._sent_s + typical <= self._max
```

为什么两道限制都要：

- **声数上限**：直接保证"用户不会听到一串语气词"，语义最清晰
- **时长上限**：防 LLM 卡死时无限循环（`max_clips` 调大时仍需它）

设 `1` 的理由：真人思考时「嗯…」一次，然后直接说正文。
要允许更长的等待表现，把 `MAX_FILLER_CLIPS` 调到 2 即可。

## 四、验证

### 4.1 逻辑复现

修复前模拟 `_send_fillers`（无节流）：

```
发出填充音: 4 声，累计 2.87s
```

修复后：

```
发出填充音: 1 声，累计 0.77s
[OK] 只发 1 声，符合预期
```

### 4.2 真实协议链路

用协议客户端模拟板子跑一轮（上传录音 → 等回复）：

```
<- playback_start
收到音频 33796 字节 (0.70s)
```

**33796 字节正好是 `filler_um.pcm` 的大小** —— 一个填充音，没有连发。

服务端日志：

```
录音结束 turn=...-t74329 块=6 字节=22530 乱序=0 重复=0
流式播放开始 turn=...-t74329 采样率=24000 原因=filler
```

`原因=filler` 只出现一次。

### 4.3 测试

zjy13 全量：**525 项通过**（原 522 + 新增 3）。

新增 3 个针对性测试：

| 测试 | 锁住什么 |
|---|---|
| `test_session_defaults_to_single_clip` | 默认只发 1 声 |
| `test_send_fillers_paces_with_playback` | 每发一个都会等它播完（旧行为下会失败） |
| `test_wait_filler_played_interruptible` | 正文到达能立刻打断等待 |

同时修正了 `test_session_respects_max_seconds`：它原本按"3 秒上限下发 4 个"
写断言，现在声数上限先拦住，必须显式 `max_clips=99` 才能测到时长限制。

## 五、踩坑记录

| 坑 | 表现 | 说明 |
|---|---|---|
| `MAX_FILLER_CLIPS` 不能 patch | 测试里 `patch("...MAX_FILLER_CLIPS", 3)` 不生效 | 它作为**默认参数**在函数定义时就绑定到 `__init__`，运行时改模块常量无效。要 patch 的是 `server.tts.fillers.FillerSession` 类本身 |
| `_send_fillers` 内部 import | patch `server.ws.server.FillerSession` 无效 | 函数体里执行 `from ..tts.fillers import FillerSession`，是**调用时**读模块属性，要 patch 源模块 |
| 协议消息名 | 发 `recording_start` 收到 `session_start` | 服务端确认录音开始用的是 `session_start`，不是同名回显 |
| 缺 `protocol_version` | `bad_message: 缺少 protocol_version` | 除 `device_hello` 外的所有 JSON 都要带这个字段 |

## 六、可调参数

| 参数 | 位置 | 默认 | 说明 |
|---|---|---|---|
| `MAX_FILLER_CLIPS` | `tts/fillers.py` | 1 | 单轮最多几声。想听两次可调 2 |
| `MAX_FILLER_SECONDS` | `tts/fillers.py` | 3.0 | 总时长上限（声数放开时才有意义） |
| `FILLER_SKIP_BELOW_S` | `ws/server.py` | 1.2 | 历史平均延迟低于此值就跳过填充音 |
| `lead_sec` | `ws/server.py` | 0.8 | 正文预送量（填充音不受它影响） |

## 七、相关文件

| 文件 | 改动 |
|---|---|
| `server/ws/server.py` | `_send_fillers` 加播放节奏等待；新增 `_wait_filler_played` |
| `server/tts/fillers.py` | 新增 `MAX_FILLER_CLIPS`；`FillerSession` 支持 `max_clips` |
| `tests/test_filler_audio.py` | 修正 1 个旧测试；新增 3 个回归测试 |
