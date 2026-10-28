# WebSocket 通信协议

> 对应 V2 第十一节、第十二节。客户端（ESP32 固件）与服务端共用 `server/protocol/messages.py`。
> 协议版本：**1**

---

## 一、为什么用 WebSocket

| 方案 | 判断 | 原因 |
| --- | --- | --- |
| HTTP 轮询 | ❌ | 延迟高，服务端不能主动推 |
| HTTP 长轮询 | ❌ | 每轮重连，开销大 |
| **WebSocket** | ✅ | 双向、长连接、二进制友好 |
| MQTT | ⚠️ | 需 broker，增加部署复杂度 |
| TCP 裸 socket | ❌ | 要自己实现帧协议、心跳、重连 |

**核心需求**：服务端要主动推送 ASR 结果、LLM 流式片段、TTS 音频；开发板要持续上传录音。
HTTP 的一问一答模型做不到。

---

## 二、消息分两类

### 2.1 控制消息（JSON 文本帧）

统一信封：

```json
{
  "type": "asr_final",
  "protocol_version": 1,
  "session_id": "abc123",
  "turn_id": "abc123-t1",
  "seq": 5,
  ...业务字段
}
```

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `type` | ✅ | 消息类型 |
| `protocol_version` | ✅ | 必须为 1，不匹配服务端会断开 |
| `session_id` | 服务端出站必带 | 会话标识 |
| `turn_id` | 轮次内必带 | 轮次标识，用于丢弃过期响应 |
| `seq` | 服务端出站带 | 递增序号，客户端可用于丢包检测 |

### 2.2 音频（二进制帧）

**不使用 JSON**（V2 第十一节第 5 条明确要求）。原因：

- 16kHz 单声道 16bit，1 秒 = 32 KB
- Base64 编码后变 43 KB（+33%）
- 再加 JSON 转义与解析开销

**帧格式（小端，固定 12 字节头）**：

```
偏移  长度  字段            说明
0     1     magic = 0xA5    帧标识，用于区分音频帧
1     1     msg_type        0x01=上行录音, 0x02=下行TTS
2     2     seq             块序号（uint16，回绕）
4     4     turn_id_short   轮次短ID（uint32，CRC32 压缩）
8     4     payload_len     负载字节数
12    N     payload         PCM s16le 裸数据
```

**总长度 = 12 + payload_len**

C 语言解析（ESP32 端）：

```c
typedef struct __attribute__((packed)) {
    uint8_t  magic;       // 0xA5
    uint8_t  msg_type;
    uint16_t seq;
    uint32_t turn_short;
    uint32_t payload_len;
} ws_audio_header_t;      // 12 字节
```

---

## 三、消息类型

### 3.1 开发板 → 电脑

| 类型 | 时机 | 关键字段 |
| --- | --- | --- |
| `device_hello` | 连接后立即 | `device_id`, `firmware`, `token` |
| `recording_start` | 按下按钮 | `sample_rate`, `channels`, `bits`, `encoding`, `channel_layout` |
| `audio_chunk` | 录音中持续 | 二进制帧，见上 |
| `recording_stop` | 松开按钮 | — |
| `playback_done` | 播完 | `received_bytes`, `received_frames`（可选，用于校验） |
| `cancel` | 用户取消 | `turn_id` |
| `heartbeat` | 每 20 秒 | — |

### 3.2 电脑 → 开发板

| 类型 | 时机 | 关键字段 |
| --- | --- | --- |
| `server_hello` | 握手应答 | `audio_in`, `audio_out`, `limits` |
| `session_start` | 开始录音 | `state` |
| `asr_final` | 识别完成 | `text`, `duration_s` |
| `llm_start` | 开始生成 | — |
| `llm_delta` | 流式片段 | `text`（可能多次） |
| `llm_final` | 生成完成 | `text`, `first_token_ms`, `total_ms` |
| `tts_start` | 开始合成 | — |
| `playback_start` | 音频就绪 | `sample_rate`, `duration_s`, `bytes`, `frames`, `frame_bytes` |
| `tts_audio` | 音频数据 | 二进制帧 |
| `turn_done` | 本轮结束 | `reason`, `state` |
| `turn_cancelled` | 已取消 | `reason` |
| `heartbeat_ack` | 心跳应答 | — |
| `error` | 出错 | `code`, `message`, `retryable`, `fatal` |

---

## 四、一次完整对话的时序

```
开发板                              电脑
  │                                  │
  │──── device_hello ───────────────>│
  │<─── server_hello ────────────────│  告知音频格式
  │                                  │
  │  [用户按下按钮]                   │
  │──── recording_start ────────────>│
  │<─── session_start ───────────────│  返回 turn_id
  │──── audio_chunk (二进制) ───────>│  ← 边录边传
  │──── audio_chunk (二进制) ───────>│
  │──── recording_stop ─────────────>│
  │                                  │  ASR 识别
  │<─── asr_final ───────────────────│  开发板立即显示文本
  │                                  │  LLM 生成
  │<─── llm_start ───────────────────│
  │<─── llm_delta ───────────────────│  ← 流式，可边收边显示
  │<─── llm_final ───────────────────│
  │                                  │  TTS 合成
  │<─── tts_start ───────────────────│
  │<─── playback_start ──────────────│  含总字节数与帧数
  │<─── tts_audio (二进制) ──────────│  ← 多帧
  │<─── tts_audio (二进制) ──────────│
  │                                  │
  │  [开发板播放]                     │
  │──── playback_done ──────────────>│  上报接收量
  │<─── turn_done ───────────────────│
  │                                  │
  │  [回到待机]                       │
```

**关键点**：

1. **`turn_id` 贯穿全程** —— 过期响应靠它丢弃
2. **`asr_final` 先到** —— 用户立刻看到"听懂了"
3. **`playback_done` 由开发板上报** —— 服务端才知道何时能收下一轮
4. **音频完整性校验** —— `playback_done` 带 `received_bytes`，服务端比对

---

## 五、音频格式协商

### 5.1 服务端期望（`server_hello.audio_in`）

```json
{
  "sample_rate": 16000,
  "channels": 4,
  "bits": 16,
  "encoding": "pcm_s16le"
}
```

### 5.2 开发板实际格式（硬件核实结果）

ESP32-S3-Touch-LCD-1.85B 的 ES7210 输出 **4 通道交错**，布局 `RMNM`：

| 通道 | 内容 | 用途 |
| --- | --- | --- |
| 0 | Reference | **回声参考，不是人声** |
| 1 | Mic1 | 人声 |
| 2 | 未知 | 参与降混 |
| 3 | Mic2 | 人声 |

**服务端处理**：

```
4ch 原始 → 取通道 1/2/3（丢弃 0）→ 降混单声道 → 重采样到 16kHz → ASR
```

**实测验证**：若错误地对 4 通道取平均，电平会被回声参考拉高 **3.5 倍**，
识别结果变差。代码在 `server/ws/audio.py`，有测试锁定。

### 5.3 下行音频

TTS 输出 24000 Hz / 16bit / 单声道，按 4096 字节切帧下发。

**开发板需注意**：24000 Hz 与 BSP 默认的 22050 Hz 不同，需要：
- 重采样，或
- 播放时配置 I2S 为 24000 Hz

---

## 六、大小限制

| 项 | 限制 | 说明 |
| --- | --- | --- |
| 单条 JSON | 64 KB | 超限服务端丢弃 |
| 单个音频块 | **48 KB** | 覆盖 48kHz/4ch/100ms 极端情况 |
| 单轮音频总量 | 60 秒 | 超限报 `audio_buffer_overflow` |
| 单轮文本 | 1000 字符 | 超限报 `text_too_long` |

### 推荐分块时长

协议在 `server_hello.limits.recommended_chunk_ms` 返回 `[20, 40, 60]`。

**实测计算**：

| 配置 | 每块字节 |
| --- | --- |
| 16kHz / 1ch / 20ms | 640 |
| 16kHz / 4ch / 60ms | 7680 |
| 48kHz / 4ch / 100ms | 38400 |

**建议开发板按 20-60ms 分块**：既控制延迟，又省 ESP32 内存。

---

## 七、状态机

```
IDLE → RECORDING → ASR_PROCESSING → LLM_PROCESSING
     → TTS_PROCESSING → PLAYING → IDLE
```

| 当前 | 可进入 |
| --- | --- |
| IDLE | RECORDING, ERROR |
| RECORDING | ASR_PROCESSING, IDLE, ERROR |
| ASR_PROCESSING | LLM_PROCESSING, IDLE, ERROR |
| LLM_PROCESSING | TTS_PROCESSING, IDLE, ERROR |
| TTS_PROCESSING | PLAYING, IDLE, ERROR |
| PLAYING | IDLE, ERROR |
| **ERROR** | **IDLE**（必须有恢复路径） |

**半双工约束**：`RECORDING` 及之后的状态都不接受新的 `recording_start`，
会返回 `turn_busy`。这避免了录音期播放、播放期录音的冲突（V2 第九节）。

---

## 八、错误码

| 错误码 | 可重试 | 说明 |
| --- | --- | --- |
| `protocol_version_mismatch` | ❌ | 版本不符，**fatal，连接会断开** |
| `bad_message` | ❌ | 消息格式错误 |
| `not_hello` | ❌ | 未握手就发其他消息 |
| `unknown_type` | ❌ | 未知消息类型 |
| `turn_busy` | ✅ | 当前状态不接受新录音 |
| `audio_too_large` | ❌ | 单个音频块超限 |
| `audio_buffer_overflow` | ❌ | 单轮音频超限 |
| `text_too_long` | ❌ | 文本超限 |
| `asr_failed` | ❌ | 识别失败 |
| `asr_empty` | ❌ | 未识别到语音 |
| `llm_failed` | ✅ | LLM 调用失败 |
| `tts_failed` | ✅ | TTS 合成失败 |
| `tts_unavailable` | ✅ | TTS 服务未启动 |
| `cancelled` | ❌ | 本轮被取消 |
| `internal` | ✅ | 服务端内部错误 |

**`fatal=true`** 表示客户端应断开重连（协议不兼容，继续通信无意义）。

---

## 九、连接管理

| 项 | 值 |
| --- | --- |
| 心跳间隔 | 20 秒（客户端主动发） |
| 心跳超时 | 30 秒无消息则断开 |
| 单轮超时 | 60 秒 |
| 最大连接数 | 4（可配） |

服务端使用 `websockets` 的 `ping_interval=20, ping_timeout=20`。

---

## 十、访问控制

V2 第十七节要求"不能默认允许任意网络设备无限调用"。

实现：

1. **`device_hello.token`** 字段校验，服务端配置 `--token` 启用
2. **监听地址**：默认 `127.0.0.1`，要局域网访问需显式改 `--host 0.0.0.0`
3. **连接数上限**：`max_connections`

> ⚠️ 第一版令牌是明文比对，**仅适用于可信局域网**。
> 生产环境应换 TLS + 更强认证。

---

## 十一、实测结果

### 单轮（本地模拟器，真实 ASR/LLM/TTS）

| 阶段 | 时间点 |
| --- | --- |
| ASR 完成 | 165 ms |
| LLM 首字 | 1003 ms |
| TTS 开始 | 1005 ms |
| 首帧音频到达 | 6206 ms |
| 完成 | 6211 ms |

音频：91 帧 / 370560 字节 / 7.72 秒（客户端收到与服务端声明**完全一致**）

### 10 轮连续对话

```
成功 10/10 轮
端到端均值 6491 ms
端到端最大 9697 ms
累计音频 4544640 字节
```

**0 错误，无串轮，无音频错序。** 满足 V2 第二十节验收标准第 10 条。

---

## 十二、开发过程中发现的三个缺陷

### 缺陷 1：音频块上限估算错误

**现象**：`音频块过大: 12800 > 8192`

**原因**：按单声道估算上限（8192），但 ES7210 是 **4 通道**，同数据量是 4 倍。

**修复**：上限提到 48 KB，并在协议里返回 `recommended_chunk_ms` 引导分块。

### 缺陷 2：客户端提前退出导致丢帧

**现象**：服务端发 126 帧，客户端只收 105 帧。

**原因**：客户端收到 `playback_start` 就立即回 `playback_done`，
服务端收到后发 `turn_done`，客户端立刻退出循环——剩余音频还在路上。

**修复**：
- 客户端改为**收齐所有帧后**再发 `playback_done`
- `playback_start` 增加 `frames` 字段告知总帧数
- `playback_done` 上报 `received_bytes`，服务端比对，不一致则报错

### 缺陷 3：模拟器跨轮状态污染

**现象**：第 2 轮起不再发 `playback_done`，服务端等到 2 分钟超时。

**原因**：`_playback_reported` 标志只在构造时置 False，`start_recording` 没重置。

**修复**：每轮开始重置该标志与期望值。

> 这三个缺陷都是**协议层面**的问题，不是实现细节。
> 说明"先写模拟器验证协议"这个做法是对的——如果直接上固件，
> 每个 bug 都要重新烧录才能发现。

---

## 十三、如何测试

### 启动服务端

```powershell
# 需先启动 WSL 的 TTS 服务
wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/serve_tts_wsl.sh

# 另一个终端启动 WebSocket 服务
cd D:\xiangmudaima\zjy13
.\.venv-asr\Scripts\python.exe -m server.ws.server --host 127.0.0.1 --port 8765
```

### 用模拟器测试

```powershell
# 握手 + 心跳
.\.venv-asr\Scripts\python.exe -m tools.ws_client_sim --hello-only

# 坏消息
.\.venv-asr\Scripts\python.exe -m tools.ws_client_sim --bad-message

# 完整一轮
.\.venv-asr\Scripts\python.exe -m tools.ws_client_sim

# 10 轮连续
.\.venv-asr\Scripts\python.exe -m tools.ws_client_sim --rounds 10

# 取消
.\.venv-asr\Scripts\python.exe -m tools.ws_client_sim --cancel-after 0.3
```

### 单元测试

```powershell
.\.venv-asr\Scripts\python.exe -m pytest tests/test_protocol.py tests/test_ws_audio.py -q
```

---

## 十四、未验证项

- [ ] **真实 ESP32 固件**：全部测试用 Python 模拟器
- [ ] **Wi-Fi 丢包/乱序**：本地回环无丢包，真实网络未测
- [ ] **连接中断恢复**：客户端断网重连未测
- [ ] **并发多设备**：只测了单连接
- [ ] **TLS**：当前明文 ws://
- [ ] **开发板播放 24000 Hz**：BSP 默认 22050，需确认能否直接播
