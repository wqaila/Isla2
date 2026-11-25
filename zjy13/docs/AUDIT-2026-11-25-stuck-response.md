# 全面体检报告：「卡在正在回应 + 完全没声音」

> **日期**：2026-11-25
> **范围**：`zjy13`（服务端 + 自带精简固件）+ 板上实际运行的
> `yuanma/AI-Pet-Badge-main` 固件
> **触发**：用户要求「全面的检查 zjy13 文件夹，排除板子卡在正在回应、无声音的状况」
>
> **结论**：定位到 **11 个独立缺陷**，其中 **3 个是「卡死且无声」的直接原因**。
> 全部已修复，两个固件均编译通过，服务端测试 **556 passed**（新增 25 项回归）。

---

## 零、一句话根因

> **真机验证后追加**：本次烧录实测抓到了「完全没声音」的**最终根因** ——
> 2026-11-23 为修「声音变尖」加的 `set_fmt` 兜底，会把 I2S TX 通道
> **关掉且不再使能**，于是每次写 codec 都失败（详见第十节）。
> 这与下面这条播放收尾缺陷是两个独立问题，都必须修。
>
> 板子的播放收尾逻辑**全部写在了一个 `continue` 之后**，而那个 `continue`
> 在一个常见场景下会永久成立 —— 于是 3 秒兜底与 12 秒兜底双双成为
> **死代码**，状态机永远停在 `ST_PLAYING`（屏幕「正在回应」），
> 而 `voice_talk_start` 又会因 `ST_PLAYING` 永久拒绝新的长按。
>
> 服务端侧的三个缺陷把这个场景**从偶发变成了必然**：
> 异常路径不发结束标记、填充音期间回报 `playback_done` 被回 error
> 导致板子丢掉全部正文音频、`RECORDING` 没有任何超时。

---

## 一、缺陷总览

| # | 现象 | 根因 | 位置 | 严重度 | 状态 |
|:-:|---|---|---|:-:|:-:|
| **12** | **完全没声音（真机实测确认）** | **`set_fmt` 兜底把 I2S TX 通道关掉且不再使能** | `voice_client.c` `handle_playback_start` | **致命** | ✅ |
| 1 | 卡在正在回应、无声、按不动 | 预缓冲门的 `continue` 跳过了全部收尾兜底 → 两段看门狗是死代码 | `voice_client.c:624` | **致命** | ✅ |
| 2 | 同上 | 结束标记帧被 `turn_short` 校验连坐丢弃 | `voice_client.c:819` | **致命** | ✅ |
| 3 | 状态在播但完全无声，无任何日志 | `esp_codec_dev_write` 返回值被丢弃 | `voice_client.c:646` | **高** | ✅ |
| 4 | 卡死后连 `playback_done` 都不发 | `finish_playback()` 首行 `if (!s_playing) return;` | `voice_client.c:727` | **高** | ✅ |
| 5 | 只听到「嗯…」然后无声 | 填充音期间回报 `playback_done` → 回 error → 板子复位 → 正文全丢 | `server.py:1141` | **致命** | ✅ |
| 6 | 异常轮次板子永远收不到结束标记 | 只有成功路径发结束标记帧 | `server.py:1549` | **高** | ✅ |
| 7 | 怎么按都没反应 | `RECORDING` 是唯一没有任何超时的 BUSY 状态 | `server.py` | **高** | ✅ |
| 8 | 卡死后要干等 135 秒 | 三处看门狗都用 `PLAYBACK_MAX_GUESS_S=120` 估计值 | `server.py:632/1475/1488` | **中** | ✅ |
| 9 | 偶发断连 | 心跳超时 30s vs 固件间隔 20s，余量仅 10s | `server.py:70` | **中** | ✅ |
| 10 | 每轮必播填充音 | `_record_latency` 全仓库无调用点 | `server.py:324` | **中** | ✅ |
| 11 | 精简固件每轮必卡 | 完全不识别结束标记帧 | `zjy13/firmware/main.c:552` | **中** | ✅ |

另有 4 个连带缺陷一并修复：重复 `recording_stop` 覆盖流水线、
`_on_cancel` 不停填充音、`ERROR` 状态无法自愈、精简固件无音频也推进状态。

---

## 二、致命缺陷 1：预缓冲门让全部收尾兜底成为死代码

### 代码（修复前）

```c
while (true) {
    size_t avail = s_play_count;

    /* 开播前预缓冲 */
    if (!s_prebuf_done) {
        if (avail < prebuf_need && !s_play_draining) {
            xSemaphoreGive(s_play_mutex);
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;              // ← 从这一行往后，全部被跳过
        }
        s_prebuf_done = true;
    }
    ...
    /* 中途播空：复位预缓冲 */
    s_prebuf_done = false;         // ← 让下一轮又回到门口

    /* 结束标记 grace 兜底（3 秒） */
    if (s_got_end_marker && s_state == ST_PLAYING) { ... }
    /* 断流兜底（12 秒） */
    if (s_playing && !s_got_end_marker) { ... }
}
```

### 为什么必然发生

1. 只要「收到 `playback_start`、但音频始终凑不够 120ms 预缓冲、
   且永远等不到结束标记」，`continue` 就**永久成立**；
2. 即使偶有数据进来，`s_prebuf_done = false` 会让下一轮**重新落回门口**，
   于是 `s_idle_ticks` / `s_end_grace_ticks` 每段最多 +1，永远到不了阈值。

**结论：这两段兜底在「彻底断流」场景下是死代码，不是「可能不触发」。**

### 后果链

```
卡在 ST_PLAYING
  → 屏幕显示「正在回应」（badge_dashboard_voice(4)）
  → 无任何音频写入 codec（缓冲空）→ 完全没声音
  → voice_talk_start 检查 s_state == ST_PLAYING → 永久拒绝
  → 固件侧没有任何 PLAYING 超时自愈
  → 只能断电重启
```

### 修复

把两段兜底**提到预缓冲门之前**，判据统一用 `s_state`（对外状态机），
并改用 `finish_playback_force()`（不看 `s_playing`）。

```c
while (true) {
    ...
    /* ---- 收尾兜底必须先跑 ---- */
    if (s_state == ST_PLAYING) {
        if (s_got_end_marker) {
            if (s_end_grace_ticks++ >= 300) {   /* 3s */
                finish_playback_force();
                continue;
            }
        } else if (s_idle_ticks++ >= 1200) {    /* 12s */
            finish_playback_force();
            continue;
        }
    } else {
        s_end_grace_ticks = 0;
        s_idle_ticks = 0;
    }

    /* 预缓冲门放在后面，continue 不再能跳过兜底 */
    ...
}
```

> **为什么用 `s_state` 而不是 `s_playing`**：卡死场景下 `s_playing` 可能
> 已被提前清成 false。这正是 `WORKLOG-2026-11-23` 第十一节「踩坑复盘 错误 2」
> 记录过的同一个坑 —— 当时加了兜底却因为判据错而永不触发。

---

## 三、致命缺陷 2：结束标记帧被 `turn_short` 校验连坐丢弃

### 代码（修复前）

```c
/* 轮次校验：旧轮帧必须丢弃 */
if (tshort != s_turn_short) return;      // ← 先校验

/* 结束标记帧：负载长度为 0 */
if (plen == 0) {                          // ← 后判断
    s_got_end_marker = true;
    ...
    return;
}
```

`s_turn_short` 只在收到 `session_start` 时赋值。一旦 `session_start`
丢失或晚于音频帧到达：

```
音频帧 → tshort 不匹配 → 丢弃
结束标记 → tshort 不匹配 → 一起丢弃
结果：s_playing=true、s_state=ST_PLAYING、缓冲恒空、s_play_draining=false
     → 精确落入缺陷 1
```

而服务端此时**一切正常**，日志只有「音频已下发…等待 playback_done」，
排查时极易误判为服务端问题。

### 修复

把结束标记判断**提到轮次校验之前**，并给丢帧加限次告警：

```c
/* 结束标记帧优先处理：不带 PCM，误接受风险极低 */
if (plen == 0) { s_got_end_marker = true; s_play_draining = true; return; }

/* 音频帧才做严格轮次校验 */
if (tshort != s_turn_short) {
    if (s_turn_mismatch_logs < 3) {
        s_turn_mismatch_logs++;
        ESP_LOGW(TAG, "丢弃非本轮音频帧：帧 turn=%08X / 当前 turn=%08X"
                      "（session_start 可能丢失或晚到）", ...);
    }
    return;
}
```

---

## 四、致命缺陷 3：填充音期间回报 `playback_done` 会丢掉全部正文音频

这是本次体检中**最隐蔽**的一条，需要同时读服务端与固件才能看清。

### 完整链路（已逐行核对）

```
1. 服务端 _start_playback_session(reason="filler") → 状态 FILLER，发 playback_start
2. 板子 handle_playback_start → s_playing=true, s_state=ST_PLAYING
3. 板子播完填充音（约 0.7s）→ finish_playback() → 发 playback_done
4. 服务端此时已在 ASR/LLM/TTS_PROCESSING（FILLER 只存活一个 await）
   → _on_playback_done 判 state != PLAYING → 回 error "不是播放中"
5. 板子收到 error → 固件把**任何** error 当无条件复位：
       finish_playback(); s_state = ST_IDLE;
   → s_playing = false
6. 正文首块到达，服务端走「填充音已开会话」分支：
       **不再发 playback_start**，直接推 PCM
7. 板子 handle_binary：`if (s_playing && s_play_buf)` → s_playing 已是 false
   → **正文 PCM 全部丢弃**
8. 用户听到：只有「嗯…」，然后彻底无声；服务端空等 135 秒看门狗
```

### 修复

`_on_playback_done` 在非 PLAYING 时改为**软忽略**（只记日志，不回 error）。
这条 error 在服务端**没有任何消费方**，是纯副作用；而迟到的
`playback_done` 本身完全无害。

```python
if client.state.state != State.PLAYING:
    logger.info("忽略迟到的 playback_done turn=%s 当前状态=%s",
                client.turn_id, client.state.state.value)
    return
```

---

## 五、服务端其余修复

### 5.1 异常路径补发结束标记帧

固件唯一的**无条件**兜底是 `finish_playback_force()`，触发条件是
「收到结束标记」。而修复前全文件**只有成功路径**发结束标记，
于是异常轮次（TTS 失败 / 空闲超时 / 本轮超时 / 其它异常 / 播放看门狗）
永远收不到 → 兜底永久不可达 → 板子只能等心跳超时断连才复位。

新增幂等辅助函数并在 **6 条路径**调用：

```python
async def _send_end_marker(self, client):
    if client.sent_frames <= 0:    # 本轮没发过音频，不必发
        return
    if client.end_marker_sent:     # 幂等
        return
    client.end_marker_sent = True
    await self.send_audio(client, b"", client.next_seq())
```

| 调用点 | 说明 |
|---|---|
| `_run_pipeline` 成功路径 | 原唯一一处 |
| `except TTSUnavailableError` | TTS 服务不可用 |
| `except TTSError` | 含 20s 流式空闲超时 |
| `except asyncio.TimeoutError` | 本轮 120s 超时 |
| `except Exception` | 通用兜底 |
| `_playback_watchdog` | 播放看门狗触发 |

### 5.2 `RECORDING` 加看门狗（此前是唯一没有超时的 BUSY 状态）

`TURN_TIMEOUT_S` 的 `asyncio.timeout` 只包住 `_process_turn`，
而那个任务要**收到 `recording_stop` 之后**才创建。所以 stop 一丢，
状态永久停在 `RECORDING`：

- 后续所有 `recording_start` 全被判 `turn_busy`（不可自愈）
- `RECORDING` 也不在可打断白名单 `(FILLER, PLAYING)` 里

新增 `RECORDING_MAX_S = 90.0`（固件单轮上限 60s + 余量）与
`_recording_watchdog`，判据沿用「同轮 + 同状态」双条件（与播放看门狗同构，
不会误杀正常轮次）。在 `recording_stop` / `_cancel` / `_cleanup` /
`_finish_turn` 四处撤销。

### 5.3 播放看门狗收敛到真实时长

修复前三处 `_arm_playback_timer` 都传 `PLAYBACK_MAX_GUESS_S=120`，
于是超时恒为 `max(120+15, 20) = 135` 秒 —— `PLAYBACK_GRACE_S` 与
`PLAYBACK_MIN_TIMEOUT_S` 成了**死常量**。实测用户卡死时正是干等这么久。

修复后：
- 填充音会话传 `0.0` → 落到 `PLAYBACK_MIN_TIMEOUT_S`（20s）
- 音频发完后调 `_rearm_playback_timer_after_audio(client, turn, duration_s)`
  按**真实音频时长**重装 → `时长 + 15s`（下限 20s）

### 5.4 心跳超时 30s → 45s

固件心跳间隔 `APP_VOICE_HEARTBEAT_MS = 20000`（20 秒），原超时 30 秒
意味着**余量只有 10 秒**。丢一次心跳就是 40 秒，必然断连。
而播放期间板子**没有任何上行流量**，连接存活完全靠这个心跳。

改为 45 秒：能容忍**整整一次**心跳丢失（40s < 45s），
同时仍能在 45 秒内发现真正失联的板子（远小于原来的 135 秒）。

### 5.5 其余三处

| 缺陷 | 修复 |
|---|---|
| `_record_latency` 全仓库无调用点 → `_should_play_filler()` 恒 True，每轮必播填充音 | 在正文首块到达时记录「松手→首块」真实耗时 |
| 重复 `recording_stop` 覆盖 `client.task` → 第一条流水线泄漏（双 TTS / 双 playback_start） | 加 `client.task` 进行中检查 |
| `_on_cancel` 不停填充音 → 残留帧打上新轮 `turn_short` 混入新轮 | 补 `_stop_filler` + `_cancel_recording_timer` |
| `ERROR` 状态无法从 `recording_start` 恢复（转换表只允许 ERROR→IDLE） | 捕获后先 `reset()` 再重试一次 |

---

## 六、精简固件（`zjy13/firmware`）修复

板子上跑的是 `ai_pet_badge`，但 `zjy13/firmware` 同属本项目，
且它有**更严重的缺陷**：完全不识别结束标记帧。

```c
/* 修复前：没有 plen == 0 分支 */
s_rt.recv_bytes += pcm_len;
s_rt.recv_frames++;                       // 空帧被当普通音频计数
app_audio_play_write(pcm, 0, ...);        // len==0 → ESP_ERR_INVALID_ARG
if (s_rt.expect_frames && ...) finish();  // 流式下 expect_frames=0 → 恒假
```

**流式下每一轮都永远不收尾。** 已补上结束标记分支（放在轮次校验之前）。

同时修复：`session_start` 5 秒超时 / `recording_start` 发送失败等路径
会在**一块音频都没传**的情况下无条件推进到 `ASR_PROCESSING`，
随后 LLM/TTS/PLAYING 全落空，`app_state_busy()` 恒真 →
`start_recording` 永久返回「正忙，稍等」。改为「传过音频才推进，否则复位」。

---

## 七、验证

### 服务端测试

```
zjy13 完整套件：556 passed（原 531 + 新增 25）
```

新增 `tests/test_stuck_response_fix.py`（25 项），覆盖：
- 固件：兜底顺序、判据变量、write 返回值、早退条件、结束标记顺序、日志
- 服务端：录音看门狗、`playback_done` 软忽略、6 条路径补发结束标记、
  真实时长重装、心跳余量、`_record_latency` 调用点、去重、cancel 停填充音、
  `ERROR` 自愈、结束标记幂等
- 精简固件：结束标记识别与顺序、无音频不推进状态
- **行为断言**：录音看门狗真的把状态拉回 IDLE、迟到的 `playback_done`
  真的不产生 error、`_send_end_marker` 真的发空帧且只发一次

### 固件编译

| 工程 | 结果 |
|---|---|
| `yuanma/AI-Pet-Badge-main/firmware/ai_pet_badge` | ✅ 编译通过，`ai_pet_badge.bin` 0x88f6d0（分区 5% 余量） |
| `zjy13/firmware` | ✅ 编译通过，`zjy13_voice.bin` 0xedc30（分区 77% 余量） |

两者仅有**既有**警告（BSP 头文件的宏名、未使用的 `audio_ensure`），
无新增警告、无错误。

### 待真机验证（需用户上板）

- [x] **真机实测通过（2026-11-25 10:07）**：一轮完整对话
      状态序列 `recording → llm_processing → tts_processing → playing → idle`，
      `playback_ms=7638.7ms`，正常回报 `playback_done`，零错误，
      串口零次 codec 写失败
- [ ] 播放正常结束后屏幕能回到「语音待命」，且不再需要重启（**已观察到，待长时间确认**）
- [ ] 服务端异常（如临时停掉 TTS）后，板子能在 **3 秒内**自动复位
- [ ] 长时间连续对话（30 分钟）不再出现「卡在正在回应」
- [ ] 松手后能立刻再次长按说话（不出现「怎么按都没反应」）

---

## 八、改动清单

### `zjy13/server/ws/server.py`（+约 260 行）

| 改动 | 说明 |
|---|---|
| `HEARTBEAT_TIMEOUT_S` 30 → 45 | 容忍一次心跳丢失 |
| 新增 `RECORDING_MAX_S = 90.0` | 录音看门狗时长 |
| 新增 `ClientSession.recording_timer` | 录音看门狗任务 |
| 新增 `ClientSession.end_marker_sent` | 结束标记幂等标记 |
| 新增 `_send_end_marker()` | 补发结束标记帧（幂等） |
| 新增 `_cancel_recording_timer()` / `_arm_recording_timer()` / `_recording_watchdog()` | 录音看门狗 |
| 新增 `_rearm_playback_timer_after_audio()` | 按真实时长重装看门狗 |
| `_start_playback_session` | 填充音会话改用最小超时 |
| `_on_recording_start` | 装录音看门狗 + `ERROR` 自愈 |
| `_on_recording_stop` | 撤录音看门狗 + 重复 stop 去重 |
| `_on_cancel` | 停填充音 + 撤两个看门狗 + 清 `client.task` |
| `_on_playback_done` | 非 PLAYING 改为软忽略 |
| `_process_turn` | 超时/异常路径补发结束标记 |
| `_run_pipeline` | TTS 失败补发结束标记；记录延迟样本；音频发完重装看门狗 |
| `_playback_watchdog` | 触发时补发结束标记 |
| `_finish_turn` | 撤销录音看门狗 |
| `new_turn` | 复位 `end_marker_sent` |

### `yuanma/AI-Pet-Badge-main/firmware/ai_pet_badge/main/voice_client.c`（+约 190 行）

| 改动 | 说明 |
|---|---|
| `play_task` | 两段收尾兜底提到预缓冲门之前，判据改 `s_state`，改用 `finish_playback_force()` |
| `play_task` | 检查 `esp_codec_dev_write` 返回值，连续失败 50 次强制收尾 |
| `finish_playback` | 早退条件放宽为 `!s_playing && s_state != ST_PLAYING` |
| `handle_binary` | 结束标记帧提到轮次校验之前；丢帧加限次告警 |
| `handle_playback_start` | 扬声器打开失败时标记写失败；复位新增计数 |
| `handle_playback_start` | **`set_fmt` 后补 `enable`，修复 I2S TX 被关掉（缺陷 12）** |
| `play_task` | **写失败后丢弃该块；`avail>0` 加 `s_state == ST_PLAYING` 条件（修复死循环）** |
| 新增 `s_write_err_count` / `s_turn_mismatch_logs` | 诊断计数 |

### `zjy13/firmware/main/main.c`（+约 45 行）

| 改动 | 说明 |
|---|---|
| `handle_binary_message` | 新增 `payload_len == 0` 结束标记分支（在轮次校验之前） |
| 录音收尾 | 只有 `send_seq > 0` 才推进状态，否则复位回 IDLE |

### `zjy13/tests/`（+约 480 行）

| 文件 | 改动 |
|---|---|
| `test_stuck_response_fix.py` | **新增**，25 项回归测试 |
| `test_streaming_tts.py` | `test_ws_server_sends_end_marker_frame` 改为语义断言（原来匹配固定字面量） |

---

## 十、真机实测追加：`set_fmt` 兜底关掉 I2S TX（缺陷 12）

> 这一节是**上板实测后**才定位到的，静态审计没能发现 ——
> 因为它是「两个修复互相冲突」，单看任一处都正确。

### 现象

烧录第一版修复后，状态机不再卡死（本轮能正常走完），但**依然完全没声音**。
串口刷屏：

```
E (343373) i2s_common: i2s_channel_write(1361): The channel is not enabled
W (343373) voice: 写 codec 失败 ret=-1（已连续 1 次，state=4 avail=1024）
W (353383) voice: 写 codec 失败 ret=-1（已连续 11 次，state=4 avail=1024）
...
```

> 注意：这条 `写 codec 失败` 日志正是本次新增的诊断代码打出来的。
> 修复前它不存在，所以这个故障一直表现为「播放中但完全没声音、无任何痕迹」。

### 根因

`esp_codec_dev` 的 `_i2s_data_set_fmt()` 内部会先**关掉**通道：

```c
// audio_codec_data_i2s.c:594-599
if (dev_type & ESP_CODEC_DEV_TYPE_OUT) {
    _i2s_drv_enable(i2s_data, true, false);   // 关 TX，函数内不再使能
}
```

它设计给 `open()` 流程内部使用 —— `open()` 调完 `set_fmt` 会紧接着 `enable`。
而 2026-11-23 为修「声音变尖」在 `handle_playback_start` 里**open 之后**
又调了一次 `set_fmt` 作为兜底：

```c
esp_codec_dev_close(s_spk);
esp_codec_dev_open(s_spk, &fs);           // ← 这里 TX 被使能
...
data_if->set_fmt(data_if, ESP_CODEC_DEV_TYPE_OUT, &fs);   // ← 把 TX 关掉，没人再开
```

串口时序是决定性证据：

```
i2s tx channel enabled      ← open() 使能
Open codec device OK
i2s tx channel disabled     ← set_fmt 关掉，且不再使能
```

之后每次 `i2s_channel_write` 都报 `The channel is not enabled`，
`esp_codec_dev_write` 返回 -1 → **一点声音都没有**。

### 修复

`set_fmt` 之后显式补一次 enable：

```c
data_if->set_fmt(data_if, ESP_CODEC_DEV_TYPE_OUT, &fs);
if (data_if->enable) {
    data_if->enable(data_if, ESP_CODEC_DEV_TYPE_OUT, true);
}
```

这样「声音变尖」（时钟重配）与「有声音」（通道使能）两个修复同时成立。

### 附带修复：第一版诊断代码引入的死循环

第一版写失败处理漏了「丢弃已取出的数据」，导致：

```
写失败 → avail>0 分支末尾 continue → 下一轮又取同一块 → 又失败 → ...
→ 计数到 50 → 强制收尾（清零计数）→ 但缓冲仍有数据 → 再失败 → 再 50 次 → ...
```

实测串口每秒数十行，状态在 `ST_IDLE`/`ST_PLAYING` 间反复横跳。修复两处：

1. 写失败后**丢弃该块并让出 CPU**，不重试同一块；
2. `avail > 0` 分支增加 `s_state == ST_PLAYING` 条件 ——
   强制收尾（已 `close`）后不再尝试写 codec。

### 实测对比

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 收尾方式 | `播放看门狗触发 20s 内未收到 playback_done` | **正常 `playback_done`** |
| 服务端 `playback_ms` | `None`（从未收到） | **7638.7 ms** |
| 本轮总耗时 | 30 ~ 34 秒 | **12.9 秒** |
| 串口 `写 codec 失败` | 每秒数十行 | **0 次** |
| 串口 `The channel is not enabled` | 每帧一次 | **0 次** |
| `播放完成` 日志 | 只在强制收尾时出现 | 正常出现（95 帧 / 375050 字节） |

---

## 十一、遗留与未验证
|:-:|---|---|
| 1 | 真机验证 | 全部修复已通过编译与静态/行为测试，但**尚未上板实测**。需烧录 `ai_pet_badge` 后按第七节清单验收 |
| 2 | 串口日志无法取证 | `logs/board_serial_live.log` 全部是 `reason 201`（NO_AP_FOUND）与 `Host is unreachable`，没有一轮成功播放，无法用日志直接证实缺陷 1/2 的发生次数；结论来自代码路径推演 + `WORKLOG-2026-11-23` 的现场记录（`state=PLAYING` / `playing=false` / 收到完整 126 帧但 36s 无消息） |
| 3 | 当前网络拓扑已失效 | `wifi_config.h` 的 `BADGE_VOICE_AUTOCONNECT_HOST = 192.168.43.164`，而本机当前 IP 是 `10.1.41.110`（`192.168.43.x` 网段已不存在）。烧录前必须改这个地址，否则板子连不上服务端 |
| 4 | `PLAY_MAX_SECONDS = 8` 与 `lead_sec = 2.0` | 8 秒环形缓冲在长回复（>10s）下仍有丢帧风险，属容量限制而非缺陷，本次未改 |
| 5 | 服务端 `expect_audio_bytes` 校验是死代码 | 所有流式路径都把它清零，`expected` 恒 0，服务端**从未真正校验**板子报的 `received_bytes`。本次未动（改动面大且非本次根因） |
| 6 | `tts_server_wsl.py` 无并发保护 | 多连接同时改写 `model.model.token_hop_len` 无锁；`gen()` 被取消时 `run_in_executor` 里的 `next(it)` 不会停。属 WSL 侧引擎问题，本次未改 |
