# zjy13 ↔ zjy14 集成缺陷排查报告

日期：2026-11-19
范围：zjy13（设备接入 + 对话编排）与 zjy14（语音引擎）之间的 HTTP 契约与集成路径

## 一、方法

先做**纸面契约核对**（字段、帧格式、CRC32、音频参数），再做**实证验证**——
每个结论都用可执行脚本或真实 HTTP 请求证明，不靠读代码推断。

这一步很关键：本次三个"看起来像 bug"的结论中，**有两个经实测被推翻**。

## 二、已修复的缺陷

### P0-1 排队超时被伪装成"成功但无音频"

| 项 | 内容 |
|---|---|
| 位置 | `zjy14/custom_voice/server/routes/synthesis.py` |
| 现象 | 推理队列满时，流式接口返回 HTTP 200 + 空音频，日志无错 |

**根因**：排队超时只 `yield` 一个「长度 0」的结束标记就 `return`。
而 zjy13 的 `_iter_length_prefixed`（`client.py:453-456`）把长度 0 理解为
**正常完成**。于是排队超时被当成"合成成功"，板子静默、调用方照常收尾。

**实证**：`_repro_queue_timeout.py` 确认 zjy13 拿到 0 块 PCM 却判定 `finished=True`。

**修复**：抛 `QueueTimeoutError` → 503。

**修复过程中踩的坑（重要）**：第一版把 `raise` 写在 `generate()` 生成器**内部**，
注释还写着"此时尚未发送响应体"。**测试直接推翻了这个假设**：

```
A) 生成器内部抛异常      -> status = 200，body = b''
B) return 之前抛异常      -> status = 503
```

因为 Starlette 的 `StreamingResponse` **先发 `http.response.start` 再迭代 body**。
生成器里抛异常时 200 已发出，改不了状态码。
最终把排队检查移到 handler 层（`return StreamingResponse(...)` 之前）。

这个坑本身值得记录：**它说明"读代码想当然"有多危险**。

### P0-2 stub 引擎下流式接口永远无声

| 项 | 内容 |
|---|---|
| 位置 | `synthesis.py` 的 `for chunk in engine.synthesize_stream(...)` |
| 现象 | `engine: stub`（gpu.yaml 默认值）时流式返回 0 字节音频 |

**根因**：`TTSEngine` 协议允许两种实现风格：

- **A 真流式**：多个 `is_final=False` 音频块 + 最后 `is_final=True` 空块
  —— CosyVoice3 就是这样
- **B 一次性**：**只** yield 一个 `is_final=True` **且带完整 pcm** 的块
  —— StubEngine 就是这样（`stub_engine.py:166-176`）

路由层写的是 `if chunk.is_final: break`，在 A 下正常，在 B 下把**唯一一块
带音频的数据整个丢掉**。

**实证**：`_repro_stub_stream.py` 用 stub 引擎实测 —— `PCM 总字节 = 0`，
但 stub 内部日志明明写着 `duration_s: 7.6`。

**修复**：先看有没有 pcm 要发，再看要不要结束：

```python
if chunk.pcm:
    yield struct.pack(">I", len(chunk.pcm)) + chunk.pcm
if chunk.is_final:
    break
```

同时修掉 `x-engine` 硬编码 `"cosyvoice3"` —— 它恰好把这个故障**彻底掩盖**了：
排查时看到 cosyvoice3 就以为引擎没问题，实际是 stub。
现在改为读 `engine.stats()`，如实上报。

### P1-1 流式路径完全不落库

| 项 | 内容 |
|---|---|
| 位置 | `synthesis.py` |
| 现象 | zjy14 的合成历史永远为空 |

**根因**：只有非流式路径调用 `_record_synthesis` / `_record_failure`，
而 zjy13 的生产路径**只走流式**（`ws/server.py:1366` 是唯一调用点）。

**影响**：`/api/v1/history` 查不到真实请求、`only_failed` 恒为 0、
自动清理无数据可清、线上排障看不到 zjy14 侧发生过什么。

**修复**：新增 `_record_stream_synthesis()`，在生成器完成时落库；
失败分支也调 `_record_failure()`。

**实证**：修复后查历史，能看到 `engine=cosyvoice3 dur=7.68s` 的真实记录。

### P1-2 zjy13 侧 503 错误信息误导

| 项 | 内容 |
|---|---|
| 位置 | `zjy13/server/tts/client.py:386` |
| 原代码 | `raise TTSUnavailableError("TTS 模型未加载")` |

503 现在有多个来源（`engine_not_ready` / `queue_timeout`），
一律说成"模型未加载"会把排查带偏。改为解析响应体的 `code`/`message`。

## 三、经实测**推翻**的结论

审查提出但实测不成立的项。记录下来，避免以后重复怀疑。

### ✗ "固件没实现 0 长帧结束协议，每轮卡 120s 看门狗"

**不成立**。`yuanma/AI-Pet-Badge-main/.../voice_client.c:691` 明确处理了：

```c
/* 结束标记帧：负载长度为 0。 */
if (plen == 0) {
    s_got_end_marker = true;
    s_play_draining = true;
    ...
    return;
}
```

审查看的是 `zjy13/firmware/main/main.c` —— 但真正烧录的固件在
`yuanma/AI-Pet-Badge-main`。**这两个固件目录不是一回事**，zjy13 的
`firmware/` 从未烧录过（device_id 是 `esp32s3-01`，而板子报的是 `ai-pet-badge`）。

这也是我在本次会话早期就踩过的同一个坑，值得写进文档。

### ✗ "流式接口返回 4 字节，链路不通"

**不成立**。那是 `Invoke-WebRequest` 对二进制流的处理问题。
用 Python 原始 HTTP 客户端重测：**426260 字节 / 4 块 / 8.88s**，完全正常。

**教训**：验证二进制接口不要用 PowerShell 的 `Invoke-WebRequest`。

## 四、已核对、确认一致

| 项 | 结论 |
|---|---|
| 请求体契约 | zjy13 发 `{text, voice_id, speed}` ↔ zjy14 `SynthesisRequest` 三字段完全匹配 |
| 流式分块格式 | 两边都是 4 字节大端 `>I` 长度 + PCM16LE，`len=0` 结束 |
| 12 字节二进制帧 | `<BBHII`，magic `0xA5`、seq uint16、turn_id_short uint32、payload_len uint32，三处代码完全一致 |
| turn_id_short 算法 | zjy13 用 `zlib.crc32`，固件手写 CRC-32（`0xEDB88320`），等价 |
| 音频参数 | 24kHz / 单声道 / 16bit / 小端，四处一致 |
| 分帧与首块 | 4096B 分帧、300ms 首块、200ms 预缓冲、48KB 单帧上限，一致 |
| `/health` | zjy13 判 200，zjy14 未就绪返 503 + `ok:false`，一致 |

## 五、第二轮修复（2026-11-19 续）

第一轮修完后剩下的 7 项，本轮全部处理。

### P1-3 `turn_id` 每 100 秒重复

| 项 | 内容 |
|---|---|
| 位置 | `zjy13/server/ws/server.py::ClientSession.new_turn` |
| 原代码 | `f"{session_id}-t{int(time.time()*1000) % 100000}"` |

毫秒对 100000 取模 = **100 秒一个循环**。会话活得久一点，两轮就会拿到
同一个 turn_id，进而：

1. `TTSClient._completed_turns` 判重误拒新的一轮
2. turn_id 的 CRC32（`turn_id_short`）相同，固件会把两轮当成同一轮
   而**丢弃新帧**（`app_protocol.c` 的轮次校验）

**修复**：改用会话内递增序号 `f"{session_id}-t{turn_counter}"`。
turn_id 只需在**本连接内**唯一（去重与固件轮次校验都是按会话看的），
不必掺时间戳。保留 `t` 前缀以兼容既有日志与监控页。

### P1-4 去重集合无限增长

`_completed_turns` 原是无界 `set`，且 `reset_turn` **从未被调用** ——
长会话下只增不减。

**修复**：改成有界 `OrderedDict`（保留最近 `COMPLETED_TURNS_KEEP = 64` 条）。
去重的目的只是挡住紧随其后的重复调用，不需要记住整个进程生命周期。

### P2-1 整轮预算 60s 与队列等待 60s 冲突

`zjy13 TURN_TIMEOUT_S = 60` 包住 ASR+LLM+TTS+下发，而
`zjy14 queue_timeout_s = 60`。两者相等时，队列一拥塞，zjy14 独自就能
吃掉 zjy13 的全部预算，结果 zjy13 先超时、zjy14 仍在排队占槽位 ——
**双方都做了正确的事，却因为预算相等而必然打架**。

**修复**：`TURN_TIMEOUT_S` 调到 120（必须 > 队列等待 + 一次合成的最坏耗时）。

### P2-2 422 未映射为参数错误

zjy13 只把 **400** 映射成 `TTSBadRequestError`，但 zjy14 用
FastAPI + pydantic，校验失败返回的是 **422**。于是"文本过长 / 语速越界"
这类**不可重试**的错误落进泛 `TTSError`，被 `ws/server.py` 当成
retryable 的 `TTS_FAILED` —— 白白重试，还掩盖真实原因。

**修复**：`(400, 422)` 都映射为参数错误（非流式与流式两处）。

### P2-3 流式无 voice_id 预校验

引擎内部（`cosyvoice_engine._pick_voice`）会校验音色，但那时
**响应头已经发出**（StreamingResponse 先发 start 再迭代 body），
抛异常改不了状态码 —— 调用方只能看到"流被截断"，
完全定位不到是音色配置问题。

**修复**：在 handler 层、`return StreamingResponse(...)` 之前预校验。
实测非法音色现在返回：

```json
{"code":"invalid_request",
 "message":"未知音色 'nobody-xyz'，可用：elysia",
 "detail":{"voice_id":"nobody-xyz","available":["elysia"]}}
```

### P2-4 `pause_profile` 默认值改变韵律

zjy14 的 `pause_profile` 默认是 `natural`（180ms），会把回复按标点切成
多段、**每段独立调用模型合成**再插静音。而 zjy13 原引擎是整段一次合成。

不显式传的话，升级到 zjy14 后韵律和音色连续性会**悄悄变化**，
用户只会觉得"声音变了"但找不到原因；短段还容易触发音色漂移。

**修复**：zjy13 侧显式传 `pause_profile`，默认 `none`（整段合成），
与原有听感一致。可用 `TTS_PAUSE_PROFILE=natural` 切换。

### P3 `_iter_length_prefixed` 无长度上限

长度字段来自对端，不能无条件信任。原来读到损坏长度（如 `0xFFFFFFFF`）时
`buf` 会一直等下去：内存无界增长，直到 120s 读超时才失败，期间毫无提示。

**修复**：加 `MAX_STREAM_CHUNK_BYTES = 96KB` 上限（协议规定单块 ≤ 48KB，
留一倍余量），超限立刻抛错。

### 第二轮回归测试

新增 `zjy13/tests/test_integration_fixes.py`（13 项）。
**并且验证了测试本身有效**：临时改回旧实现后，`turn_id` 相关 2 项立刻失败；
恢复修复版后通过。测试能失败才有价值。

| 套件 | 结果 |
|---|---|
| zjy13 | **538 项全通过** |
| zjy14 | **全通过** |

### 第二轮端到端实测

```
1. pause_profile=none   status=200 engine=cosyvoice3 chunks=3 dur=6.76s  [OK]
2. 非法 voice_id         status=400 "未知音色 'nobody-xyz'，可用：elysia"  [OK]
3. 流式落库              记录数=5，最新 dur=6.76s                          [OK]
```

## 六、相关文件（含第二轮）

| 文件 | 改动 |
|---|---|
| `zjy14/custom_voice/server/routes/synthesis.py` | 排队检查移到 handler 层；`is_final` 带 pcm 时先发；x-engine/采样率读真实值；流式落库；voice_id 预校验 |
| `zjy14/custom_voice/core/errors.py` | 新增 `QueueTimeoutError`（503） |
| `zjy14/tests/test_queue_timeout.py` | **新增** 5 项回归测试 |
| `zjy14/tests/test_errors.py` | 补 `QueueTimeoutError` 映射断言 |
| `zjy13/server/tts/client.py` | 503 错误信息解析响应体；422 映射；`pause_profile` 显式传递；`_completed_turns` 有界化；分块长度上限 |
| `zjy13/server/ws/server.py` | `turn_id` 改用递增序号；`TURN_TIMEOUT_S` 60→120 |
| `zjy13/tests/test_integration_fixes.py` | **新增** 13 项回归测试 |

## 七、剩余项

本轮已修完全部已识别问题。以下为**设计层面的取舍**，非缺陷：

| 项 | 说明 |
|---|---|
| 流式路径仍无去重 | `_active_turns` 只在非流式的 `synthesize()` 里检查。生产只走流式，所以去重实际不生效。若要加，需在 `synthesize_stream` 里维护 per-turn 状态，并明确"重试是否允许"的语义 —— 属于新功能而非修 bug |
| `x-first-audio-ms` 头缺失 | 非流式首音频指标恒为 `None`。zjy14 未提供该头，属未实现功能 |
