# zjy13 / yuanma 补充代码审查报告

> 背景：原 [`代码审查报告.md`](代码审查报告.md) 写于 2026-09-21，只覆盖当时 6 个项目，
> **未覆盖 `zjy13` 与 `yuanma`**。本报告补齐这两个项目。
>
> 审查方式：按模块拆成独立审计单元并行静态阅读，
> 逐行阅读 Python 与 C 源码，关键结论到 ESP-IDF 5.5.5 与 `esp_websocket_client` 源码交叉验证。
>
> **统计（2026-11-09，含补充审计）**：
>
> | 级别 | 2026-11-05 初版 | 2026-11-09 补充 | 合计 |
> |---|---:|---:|---:|
> | 高危 | 10 | 5 | **15**（`H1`–`H15`） |
> | 中危 | 27（仅 19 项可追溯） | +14 | **41**（`M1`–`M41`） |
> | 低危 | 26（无编号） | 15 条编号化 | **15 条编号**（`L1`–`L15`）+ 6 类线索 |
> | 判定"不是问题" | 3 | 18 | — |
>
> 初版的问题在于：中危 `M20`–`M27` 共 8 项**丢失了位置与描述**，低危 26 项只有类别概述，
> `yuanma` 审计单元输出被截断。2026-11-09 按模块重跑 4 个单元补上了这些缺口，
> 完整回溯见 [`代码审查报告-补充审计-2026-11-09.md`](代码审查报告-补充审计-2026-11-09.md)。
>
> ⚠️ 补充审计条目为**自动审计产出**，抽样复核 5 条中 4 条成立、1 条误报已剔除；
> 修复前请按文件行号自行确认。修复状态见 [`代码审查修复台账.md`](代码审查修复台账.md)。

---

## 一、项目与代码规模

| 项目 | 自研代码 | 说明 |
| --- | --- | --- |
| `zjy13` | 69 文件 / 约 512 KB | `server`（19 py）、`tests`（16）、`tools`（15）、`firmware`（13 c/h）、`asr`（4） |
| `yuanma` | 50 文件 / 约 428 KB | `firmware`（35）、`tools`（13 py）、`tests`（2） |

---

## 二、高危问题（10 项，建议优先修）

### 2.1 zjy13 服务端

#### H1. 下行音频 `seq` 按 1 字节打包，第 256 帧起回绕

- **位置**：`zjy13/server/protocol/messages.py`（`AudioFrame.encode`）
- **证据**：`struct.pack("<BBHII", MAGIC, msg_type & 0xFF, seq & 0xFFFF, ...)` 格式串里 `seq` 是 `B`（1 字节），不是 `H`
- **影响**：与固件 `tools/fw_protocol_equiv.py` 按 `data[2] | data[3]<<8`（uint16）解析不一致；
  第 256 帧（约 32 秒音频）后 `seq` 回绕成 0，固件读到的序号变乱值；
  本仓库自身的 `decode()` 也会把 `seq>=256` 解析成非法 `msg_type`
- **建议**：统一为 2 字节 `seq`（格式串 `<BBHHII` 或重排头字段），两端同步，补 `seq=300` 往返用例

#### H2. LLM 读超时 30s 恒小于整轮总超时 90s

- **位置**：`zjy13/server/llm/client.py`（httpx Timeout 构造）；`zjy13/server/config.py`（默认值）
- **证据**：`read=cfg.request_timeout_s`（默认 30s），而 `total_timeout_s=90s` 只用于预算拦截
- **影响**：长回复（尤其开启 thinking）必然在 30s 被 `ReadTimeout` 掐断；
  而 `LLMTimeoutError.retryable=True`，未吐字时会重试最多 3 次 → **同一问题被重复计费**
- **建议**：`read` 超时覆盖整轮，或用 `asyncio.timeout(total)` 做真正的总预算

#### H3. `cancel()` 抛 `CancelledError`，取消分支成死代码并导致 turn_id 混淆

- **位置**：`zjy13/server/llm/client.py`（`task.cancel()`）、`zjy13/server/dialogue/session.py`（turn_id 生成）
- **证据**：代码只捕获 `LLMCancelledError`，但 `task.cancel()` 抛的是 `asyncio.CancelledError`；
  `turn_id` 由 `len(self._turns)+1` 生成，而被取消的轮次也会追加进 `_turns`
- **影响**：取消路径错误处理不一致；turn_id 编号错位，可能触发 `LLMDuplicateTurnError` 永久失败；
  在途 httpx 连接未读完，连接池泄漏
- **建议**：`chat()` 内把 `CancelledError` 转为 `LLMCancelledError`；turn_id 改用独立自增计数器

### 2.2 zjy13 固件（`zjy13_voice`）

#### H4. 64KB 静态 JSON 副本占内部 RAM，但实际只能处理 <16KB 消息

- **位置**：`zjy13/firmware/main/main.c`（`static char s_json[APP_MAX_JSON_BYTES]`，64KB）
- **证据**：上游 `app_net.c` 的重组缓冲只有 `WS_REASM_TEXT_MAX = 16384`
- **影响**：白白占用 64KB 内部 DRAM（ESP32-S3 内部 SRAM 仅 512KB），挤占 I2S DMA 与 WiFi 缓冲；
  超过 16KB 的消息在上游就被丢弃，`s_json` 的容量检查分支永不成立
- **建议**：两者统一为同一常量；或改 `heap_caps_malloc` 按需分配

#### H5. 重连任务与 WS 事件回调竞争 `s_ws`/`s_ws_connected`

- **位置**：`zjy13/firmware/main/app_net.c`
- **证据**：重连任务读 `s_ws_connected` 并调 `esp_websocket_client_stop/start`；
  事件回调在组件任务上下文写 `s_ws_connected`；二者无锁。
  `app_ws_send_text` 只判空 `s_ws` 就调用组件 API，而退出路径会 `destroy` 并置 NULL
- **影响**：断线瞬间存在 use-after-free 与重复 start，可能崩溃（LoadProhibited）
- **建议**：用互斥量或事件组统一管理连接生命周期；销毁前置 `dying` 标志并等待发送方退出

#### H6. I2S 通道在未 enable 时调用 `reconfig_*_clock`

- **位置**：`zjy13/firmware/main/app_audio.c`
- **证据**：`i2s_channel_disable()` 返回值被丢弃；未 enable 时它返回 `ESP_ERR_INVALID_STATE`
- **影响**：`mode` 切换顺序不当时 `reconfig` 在 RUNNING 态被调用并失败，
  整轮录音/播放不可用，状态机停在 `RECORDING`/`PLAYING`
- **建议**：检查 `disable` 返回值（`INVALID_STATE` 可忽略），封装 `disable → reconfig → enable`

#### H11. 下行音频帧按「声明长度」写入，未与实收字节核对 → 读越界 PSRAM

- **位置**：`zjy13/firmware/main/app_protocol.c:111-117`（`app_proto_decode_audio`）、
  `zjy13/firmware/main/main.c:532-541`（`handle_binary_message`）
- **证据**：解码只校验「实收 ≥ 声明」——`if (len - APP_BINARY_HEADER_SIZE < hdr->payload_len)`；
  调用方却按**声明值**取长度去写 I2S：
  ```c
  const uint8_t *pcm = data + APP_BINARY_HEADER_SIZE;
  const size_t pcm_len = hdr.payload_len;   /* len=3000，payload_len=4000 时越界 1000 */
  ```
- **影响**：声明长度大于实收长度时，`app_audio_play_write` 会读取缓冲外的 PSRAM。
  小帧读到邻近堆内存（杂音），大帧越过分配边界 → `Cache disabled but cached memory region accessed`
  或 LoadStoreError 硬复位。分片重组按实收长度判定「收满」，恰好放过了这个不一致
- **建议**：解码强制 `len == APP_BINARY_HEADER_SIZE + hdr->payload_len`，
  或让调用方用 `len - APP_BINARY_HEADER_SIZE` 作为写入长度；并校验 `pcm_len % 2 == 0`

#### H12. 心跳超时用「最后收到服务端消息的时间」，空闲 30 秒即误判断线

- **位置**：`zjy13/firmware/main/main.c:459, 626-632`；`app_net.c:472-475, 498-499`
- **证据**：`last_msg_ms` 只在收到**服务端**消息时刷新，本地发出的心跳不计入；
  `heartbeat_task` 用它判定超时。`app_main` 不检查 `app_net_wifi_init` 的返回值
- **影响**：用户长按说话前静置 30 秒，心跳任务就判定断线并尝试 `app_ws_restart()`；
  而 `s_ws == NULL`/未连上时 `restart` 静默返回，既不重连也不报错。若又在 Wi-Fi 22 秒超时前
  发 `R`，`record_task` 因 `!app_ws_connected()` 直接退出，**从头到尾没产生任何鉴权记录**，
  设备永久离线直到手动复位
- **建议**：发心跳时同样刷新 `last_msg_ms`；超时后周期性重试 `app_ws_restart()`；
  `app_net_wifi_init` 失败要走显式重试路径

### 2.3 yuanma 固件（板子上实际运行的 `ai_pet_badge`）

> 已知历史问题（`record_loop` 返回路径崩溃、下行分片未重组、WiFi 省电卡死、静态缓冲超配）已修复，不再重复报告。

#### H7. 播放任务可能在临界区被 `vTaskDelete`

- **位置**：`yuanma/.../main/voice_client.c`
- **证据**：`s_task` 的读-改-写跨任务无保护；`handle_text` 里 `vTaskDelete(s_task)` 与
  `record_loop` 尾部的自删存在竞态
- **影响**：持锁/刚释放互斥量的任务被删除，FreeRTOS 调度器断言或内存破坏 → 随机硬复位
- **建议**：不外部 `vTaskDelete`，改为置标志让任务自行退出；`s_task` 操作纳入同一把锁

#### H8. 断线时从不清理录音/播放状态

- **位置**：`yuanma/.../main/voice_client.c`（`WEBSOCKET_EVENT_DISCONNECTED` 分支）
- **证据**：断线分支只置 `s_connected=false; s_playing=false; s_state=ST_CONNECTING`，
  不收 `s_task`、不补发 `playback_done`；重连成功后又无条件覆盖 `s_state=ST_IDLE`
- **影响**：服务端一轮会话永远收不到 `playback_done`/`recording_stop`，两端状态不同步，
  之后表现为"能连上但一说话就停"
- **建议**：断线时做完整收尾（终止标志、清 turn 状态、统一回收资源），重连时不要无条件覆盖状态

#### H9. PSRAM 分配失败的回退路径尺寸错误 → 环形缓冲越界写

- **位置**：`yuanma/.../main/voice_client.c`
- **证据**：`PLAY_BUF_BYTES` 按 8 秒（384000 字节）定义，
  但回退到内部 RAM 时只按 6 秒分配；环形边界判断仍用 `PLAY_BUF_BYTES`
- **影响**：一旦 PSRAM 碎片化导致大块分配失败，索引会在缓冲区之后约 96KB 处继续写，
  **破坏相邻内部 RAM**（任务栈/LCD DMA 描述符）——属于堆越界写
- **建议**：用运行期变量记录真实容量，所有取模/边界判断改用它

#### H10. 明文 `ws://` + token 字段硬编码为空

- **位置**：`yuanma/.../main/voice_client.c`（握手消息 `"token":""`；`ws://` + `OVER_TCP`）
- **影响**：同局域网任何主机可冒充设备；语音音频与 LLM 回复全程明文可嗅探
- **建议**：增加共享密钥/一次性 token（NVS 或配网下发），提供 `wss://` + `crt_bundle` 分支

> 以下 H13–H15 为 2026-11-09 补充审计新增（原审计单元输出被截断，未覆盖到）。

#### H13. 二进制分片超长被丢弃后，后续分片从缓冲区头部继续拼 → 该连接此后帧全错

- **位置**：`yuanma/.../main/voice_client.c`（`ws_event` 的二进制分片处理）
- **证据**：超长丢弃分支只 `s_bin_len = 0` 就 `break`，没有标记「本条消息作废」；
  下一个分片仍按 `payload_offset` 写入 `s_bin_buf[0]` 起
- **影响**：一次超长帧之后，该连接收到的所有音频帧都按错误偏移拼接，整轮音频错位/杂音，
  直到断开重连才恢复；用户感知为「说着说着声音就乱了」
- **建议**：超长时置 `s_bin_bad = true` 并丢弃该消息的**全部**后续分片，直到 offset 归零

#### H14. `voice_start` 失败不清 `s_ws`，自动重连永久失效；缓冲分配失败则永久卡在 `ST_RECORDING`

- **位置**：`yuanma/.../main/voice_client.c:745-790`（`voice_start`）、`:274`（`record_loop` 开头）
- **证据**：`voice_start` 开头 `if (s_ws) return ESP_OK;`，而连接/启动失败后 `s_ws` 未必被清空；
  `record_loop` 第一行 `if (!s_mic_buf || !s_frame_buf) return;` —— 缓冲为 NULL 时直接退出且
  不改状态机
- **影响**：服务端没起或首次连接失败时，`badge_network` 认为「已经启动」，此后不再尝试重连；
  缓冲分配失败（PSRAM 碎片）时状态停在 `ST_RECORDING` 且没有录音任务，界面显示「在听你说」，
  实际零采集，且再次触发会复用失效缓冲区
- **建议**：失败路径显式 `s_ws = NULL`；`record_loop` 分配失败时置错误状态并上报，
  不要静默 return

#### H15. `voice_stop` 可能在发送线程仍阻塞时销毁 WS 客户端 → use-after-free

- **位置**：`yuanma/.../main/voice_client.c`（`voice_stop`）
- **证据**：`voice_stop` 直接 `esp_websocket_client_destroy(s_ws)` 并置 `s_ws = NULL`，
  不等待正在 `send_bin` 的 `record_loop` 退出；发送侧只判 `s_ws` 非空就调用组件 API
- **影响**：USB 串口 `VOICE STOP` 或长按结束时销毁与发送并发，发送侧持有已释放句柄 →
  LoadProhibited 崩溃（与 H5 同类，但触发路径是主动 stop 而非断线）
- **建议**：置 `stop_requested` 标志并等待录音任务退出（带超时）后再 destroy；
  或与 H5 一并纳入统一的连接生命周期管理

---

## 三、中危问题（41 项）

> **2026-11-09 补齐**：本节原先只有 19 项（`M1`–`M19`），缺失的 `M20`–`M27` 没有任何记录。
> 本轮按模块拆 4 个审计单元重跑（见 [`代码审查报告-补充审计-2026-11-09.md`](代码审查报告-补充审计-2026-11-09.md)），
> 补齐了 8 项中危并新增 14 项，现共 41 项。新增条目的**来源单元与复核状态**见该补充报告。

### 3.1 zjy13 服务端

| # | 位置 | 问题 |
| --- | --- | --- |
| M1 | `ws/server.py` | TTS 输出文件名直接拼 `turn_id`，且服务端从不清理 WAV，长期运行占满磁盘 |
| M2 | `ws/server.py` | `TURN_TIMEOUT_S=60s` 小于「TTS 超时 120s + 按播放进度节流下发」的实际耗时，长回复必然超时 |
| M3 | `ws/server.py` | `recording_start` 的音频格式参数无类型/范围校验，畸形值导致静默断连 |
| M4 | `ws/server.py` | 音频下发与 JSON 发送异常降级为 debug 日志，turn 仍按成功收尾，客户端永远收不到完整音频 |
| M5 | `llm/client.py` | `_completed_turns` 只增不减，全局单例长期运行内存增长；turn_id 用时间戳取模会回绕碰撞 |
| M6 | `llm/client.py` | 流式响应在 token 上限处截断（无 `[DONE]`）时 `finish_reason=None`，半句进 TTS 且污染历史 |
| M7 | `dialogue/session.py` | `send()` 只捕获 `LLMError`，取消与配置错误既不入记录也不清理 active 状态 |
| M8 | `dialogue/character.py` | system 提示词由角色卡整段插值，角色卡含 `{}` 会崩；user 输入无长度/内容清洗，存在提示词注入 |
| M9 | `config.py` | `base_url` 只校验协议前缀，允许明文 `http://` 传输 API Key；`redacted()` 会打印含凭据的代理 URL |

### 3.2 zjy13 固件

| # | 位置 | 问题 |
| --- | --- | --- |
| M10 | `main.c` | 下行帧"重复/乱序"判断条件写错（用 `recv_frames` 当期望序号），去重失效或误删合法帧 |
| M11 | `app_protocol.c` | JSON 字符串扫描用 `end[-1]` 判断转义，连续反斜杠时误判，字段解析可被带偏 |
| M12 | `app_net.c` | 连接超时路径先 `stop` 又立即 `start`，且未检查 `stop` 返回值 |
| M13 | `app_audio.c` | `app_audio_init` 任一步失败即返回，已建的 I2C/I2S 资源不清理，无法重入 |
| M14 | `main.c` | 断线回调直接改 `s_rt.playback_active`，多任务无锁读写 `runtime_t` |
| M15 | `app_protocol.c` | `strtol` 不检查 `ERANGE`，超长数字被截断成 `LONG_MAX`，`expect_bytes` 永不相符 |
| M16 | `main.c` | 播放写入超时只打日志继续，丢失的 PCM 造成断续，且 `recv_bytes` 仍按收到量累计 |
| M17 | `main.c` | `xTaskCreate(record_task)` 返回值未检查，失败时状态机永久卡在 `RECORDING` |
| M18 | `main.c` | `record_task` 退出路径在从未开始录音时也发 `recording_stop`，且不检查连接 |

### 3.3 yuanma 固件

| # | 位置 | 问题 |
| --- | --- | --- |
| M19 | `voice_client.c` | `s_mic`/`s_spk` 多任务无锁 check-then-act，且跨任务 close，可能重复 init 或互关句柄 |

### 3.4 zjy13 服务端 · 2026-11-09 补充（WS / 协议）

| # | 位置 | 问题 | 来源 |
| --- | --- | --- | --- |
| M20 | `ws/server.py:320-342,747` | `max_connections=4` 只有声明从未被读取；`access_token` 为空时握手完全不校验 → 局域网内任何主机都能驱动整条链路（真实调用 LLM/TTS，产生费用与显存占用）。`protocol.md` 把「最大连接数 4」列为已实现 | A |
| M21 | `ws/server.py:413-432, 286, 411` | `client.task` 只覆盖不取消 + `_recv_loop` 串行处理 → 旧轮 `_finish_turn` 可在新录音期间把 `state` 写回 IDLE。`_on_cancel` 的 `state.reset()` 无差别清掉进行中的录音/播放 | A |
| M22 | `ws/server.py:518, 475-489` | `asyncio.to_thread` 调 FunASR，超时/取消后底层线程仍在跑（`to_thread` 不随取消停止），下一轮立刻再次 `generate` → 同一 `AutoModel` 实例并发推理，显存翻倍/串扰 | A |
| M23 | `llm/client.py:170-173, 196-203` | `_current_task` / `_cancel_event` 是进程级单例，`LLMClient` 被所有连接共享 → **A 连接的 cancel 会掐掉 B 连接的轮次**，被取消方停在 `LLM_PROCESSING` 永久 `turn_busy` | A+B |
| M24 | `ws/server.py:157-165` | `send_json` 超过 `MAX_JSON_BYTES` 时静默丢弃且不回报调用方 → `server_hello`/`llm_final` 丢失时上层仍按成功继续，无可观测错误码 | A |
| M25 | `protocol/messages.py:349-356` | `validate_inbound` 用 `int(ver)` 转版本号，`"1.0"`/`"v1"` 抛 `ValueError` 而非 `ProtocolError`，`_on_json` 只捕获后者 → **连接被直接断开**而非回 `bad_message` | A |

### 3.5 zjy13 服务端 · 2026-11-09 补充（LLM / TTS）

| # | 位置 | 问题 | 来源 |
| --- | --- | --- | --- |
| M26 | `tts/client.py:290-293` | 输出文件名直接拼 `turn_key`/`output_name`，只对 `voice_id` 做了清洗 → 路径穿越/越界覆盖（当前调用方尚安全，接口是公开的） | B |
| M27 | `tts/client.py:252-284` | TTS 无状态码分类：503（模型热加载）与真 500 一视同仁；且**整个合成无重试/退避**（对比 LLM 层有 `max_attempts`） | B |
| M28 | `llm/client.py:187, 205-207` | `_completed_turns` 只对**成功轮**登记，失败轮不写 → 同一 `turn_id` 可无限重复提交、重复计费；TTS 侧相反（成功即永久登记）。两客户端幂等语义不一致 | B |
| M29 | `llm/adapter.py:189-192` | 流中错误对象一律判为不可重试的 `LLMProtocolError` → 服务商的 `rate_limit_exceeded`/`overloaded` 本可重试却被直接判死 | B |
| M30 | `ws/server.py:120-123` + `tts/client.py:237-240` | `turn_id = ...{int(time.time()*1000)%100000}`，约 100 秒回绕 → 同 session 撞号触发 `TTSDuplicateError`，表现为「100 秒后整轮 tts_failed」（与 M1 的磁盘残留是不同根因） | A |

### 3.6 zjy13 对话层 + 固件 · 2026-11-09 补充

| # | 位置 | 问题 | 来源 |
| --- | --- | --- | --- |
| M31 | `main.c:469-474` | `protocol_version` 缺失/为浮点/为 null 时**整条控制消息被静默丢弃**（含 `session_start`），设备端在 5 秒等待上白等，界面只显示「识别中...」 | C |
| M32 | `main.c:326-345, 356-372` | `llm_delta` 被丢弃、`seq` 不连续性完全未检测 → 流式 UI 失效（只在 `llm_final` 一次性出全文）；丢帧不计入 `recv_bytes`，表现为偶发 `audio_mismatch` 断音 | C |
| M33 | `dialogue/session.py:165, 212` | `turn_id` 用 `len(_turns)+1` 生成，失败轮与取消轮都占位 → 编号跳跃且被 `.ok` 过滤后留下历史空洞，服务端日志无法对齐（与 H3 的 turn_id 混淆同源但切入点不同） | C |
| M34 | `dialogue/session.py:176` + `guards.py:21` | 空历史守卫触发条件错了：只要上一轮失败/被兜底，`history` 就为空 → 用户问「刚才说到哪」被硬编成「我们刚开始聊」，与事实矛盾；正则 `.` 不跨行，遇换行反而漏判 | C |
| M35 | `character.py:316-322` + `session.py:198-203` | 历史裁剪按**消息条数**（默认 20 条）而非字符预算，单条可达 512 token → 长会话 prompt 无上界；`soft_truncate` 的 `max_chars` 路径从未被传入 | C |

### 3.7 yuanma 固件 · 2026-11-09 补充

| # | 位置 | 问题 | 来源 |
| --- | --- | --- | --- |
| M36 | `voice_client.c`（`play_task` / `finish_playback`） | 播放任务只查 `s_spk` 指针非空、不查是否 open；与新 `playback_start` 的 `head/tail/count` 清零交错 → 跨任务写已 close 的 codec，新旧轮采样混流吞字 | D |
| M37 | `voice_client.c`（`record_loop` / `handle_playback_start`） | 录音与播放共用一条 I2S（16k vs 24k）但 open/close 无互斥；`read` 失败分支是 `continue` 不退出 → 时钟被重配、录音任务空转 | D |
| M38 | `voice_client.c`（`ws_event` 二进制分片） | 分片超长丢弃时只置 `s_bin_len=0` 就 break，后续分片从 `s_bin_buf[0]` 继续拼 → 该连接此后帧错位、整轮音频丢失 | D |
| M39 | `voice_client.c:745-790, 274` | `voice_start` 失败路径不清 `s_ws`，`voice_is_started()` 恒为真 → `badge_network` 自动连接永久不再重试（弱网/服务端未起时极常见）；`record_loop` 缓冲为 NULL 时直接 return，`s_state` 永久停在 `ST_RECORDING` | D |
| M40 | `voice_client.c`（`voice_stop`） | `voice_stop` 可能在 `record_loop` 仍阻塞于 `send_bin` 时 destroy WS 并置 `s_ws=NULL` → use-after-free（USB `VOICE STOP` 可复现） | D |
| M41 | `badge_network.c`（`network_event` / `run_network`） | Wi-Fi 事件回调内同步 `esp_wifi_connect`，与每 10 秒的定时重连重入；无返回值检查、无退避 | D |

---

## 四、低危问题

> **2026-11-09**：本节原先是 6 行类别概述、无任何编号。本轮补充审计产出 15 条可编号条目（`L1`–`L15`），
> 原 6 类概述保留在下半部分，作为**尚未逐条展开**的线索。

### 4.1 可编号条目（2026-11-09 补充）

| # | 位置 | 问题 | 来源 |
| --- | --- | --- | --- |
| L1 | `ws/server.py:434-464` | `_on_playback_done` 只校验 `received_bytes`，`expect_audio_frames` 从未被读取 → 协议文档所称「帧/字节双重校验」只实现一半 | A |
| L2 | `ws/server.py:411, 220-227` | `_process_turn` 从不把 `client.task` 置 None，`_cleanup` 只取消未完成任务 → 已完成任务引用常驻，两个 `_finish_turn` 的 `turn_done` 可乱序 | A |
| L3 | `ws/audio.py:93-121` + `server.py:400-404` | 空 payload 帧也计入 `chunks` → `recording_stop` 以块数判空，纯空帧可通过校验并白跑一轮状态机 | A |
| L4 | `llm/client.py:375-381` | 200 但 body 非 JSON（网关 WAF 页）时报「响应不是合法 JSON」，不带 `status_code` 也不带响应片段，无法判断是谁拦的 | B |
| L5 | `llm/client.py:416-421` | 紧跟 `raise_for_status` 的 `raise LLMProtocolError` 是不可达死代码，掩盖了 3xx 未跟随重定向的真实场景 | B |
| L6 | `config.py:173-189` | `redacted()` 只脱敏 `api_key`，`proxy`/`base_url` 中 `user:pass@` 与 `?key=` 原样输出（当前无调用点，故低危） | B |
| L7 | `llm/base.py:70-72` + `session.py:196-208` | `LLMResult.empty` 定义了但从未接入：空白回复作为成功轮写入 `_turns`，历史缺一轮而统计记一次失败 | B |
| L8 | `session.py:52` | `to_dict()` 用真值判断 `first_token_ms`，`0.0` 被记成 `None`，首字延迟均值样本偏少 | C |
| L9 | `main.c:304-312` | `session_start` 与 `playback_start` 重复复位 `recv_*`，跨轮次 `seq` 不归零 → 上一轮迟到帧被累加到新一轮 | C |
| L10 | `app_ui.c:211-230` | `app_ui_append_reply` 的 `strlen` 在锁外，与 `clear_reply` 竞态 → `room` 基于过期长度，回复文字错乱 | C |
| L11 | `app_state.c:47-50, 102-110` | `app_state_get` 不持锁，`busy` 判断与 `try_enter` 之间存在 TOCTOU，表现为偶发「按了没反应」 | C |
| L12 | `voice_client.c`（`json_get_int`/`json_get_str`） | `json_get_int` 不查 `ERANGE`（溢出变 -1 → `(uint32_t)4294967295`，是 M38 的成因）；`json_get_str` 仅判 `v[-1]=='\\'`，`\"` 处理错误 | D |
| L13 | `voice_client.c`（`fetch_weather`） | `run_network` 中同步 TLS（最长 12 秒），与首次下行时 `s_bin_buf` 的 PSRAM 分配峰值竞争 → 分片缓冲分配失败 + M38 错位 | D |
| L14 | `voice_client.c`（`ui_sync`） | `switch(s_state)` 与写 `badge_dashboard_voice` 之间无原子性，4 个任务并发写 `s_state`，且 `record_loop` 绕过 `ui_sync` 直写 → 语音状态显示错乱 | D |
| L15 | `voice_client.c`（`handle_session_start`） | `s_play_draining` 仅靠 frames 匹配置位；frames 缺失/溢出时永不置位，且 `play_task` 无超时兜底 → 永久 `ST_PLAYING`、TALK 永久「正忙」 | D |

### 4.2 尚未逐条展开的类别线索（原 26 项摘要）

| 类别 | 代表项 |
| --- | --- |
| 认证/安全 | `device_hello` 可重放无速率限制、token 非恒定时间比较；错误响应体原样下发客户端（可能含服务商内部信息） |
| 资源泄漏 | ASR `to_thread` 取消后线程仍跑、临时目录异常路径清理不全；`tts` 生产 WAV 不回收 |
| 健壮性 | `Retry-After` 只解析数字形式；408 未标可重试；多处 `esp_err_t` 返回值被忽略 |
| 边界/类型 | `playback_start` 的 bytes/frames 强转 uint32 未校验负数；`max_chars` 分支截断标记写反 |
| 构建配置 | `CMakeLists` 强制覆盖 `CONFIG_APP_UI_LCD_ENABLE=0` 使 Kconfig 失效；storage 分区未占满 16MB |
| 一致性 | 记录里存截断后文本，但 `on_delta` 已把未截断原文推给上层，前后端文本不一致 |

---

## 五、跨文件核对后判定"不是问题"的项（3 项）

避免误报，以下嫌疑经核对后排除：

1. **命令注入 / 路径穿越**（zjy13 server）：8 个审查文件内无 `subprocess`/`shell=True`；
   角色卡路径来自 CLI 与 `.env`（服务端自身配置），非远程输入
2. **硬编码密钥**：`zjy13` 服务端全部走环境变量，测试里的 `sk-very-secret-value` 只是断言"不泄露"的假值；
   `yuanma` 固件的 `APP_DEVICE_TOKEN`/`APP_WIFI_PASSWORD` 均来自 Kconfig 且默认空串
3. **WS 分片重组越界**（`zjy13` 固件 `app_net.c`）：`need` 检查在 `memcpy` 之前，
   按累计长度寻址，`payload_offset==0` 时重置——边界正确

---

## 六、修复优先级建议

### 第一优先（会导致安全泄露或功能失效）

| 顺序 | 问题 | 理由 |
| --- | --- | --- |
| 1 | **H10** 明文 `ws://` + 空 token | 安全；局域网内可冒充/嗅探 |
| 2 | **H2** LLM 读超时 < 总超时 | 稳定打断长回复，且**重复扣费** |
| 3 | **H1** 下行 seq 1 字节 | 32 秒以上音频序号错乱，与固件不一致 |
| 4 | **H9** 回退缓冲尺寸错误 | 堆越界写，破坏相邻内存 |
| 5 | **H7** 播放任务临界区被删除 | 随机硬复位，与历史崩溃症状同类 |

### 第二优先

- **H3** 取消路径（turn_id 混淆 → 永久失败）
- **H4** 64KB 静态缓冲（挤占内部 RAM，与已修复的 LCD 内存问题同源）
- **H5/H6/H8** 固件竞态与状态清理
- **M2** TURN_TIMEOUT 与节流下发矛盾

### 第三优先

- 其余中危与低危项，建议随迭代批量清理

---

## 七、与既有报告的关系

| 报告 | 覆盖范围 | 日期 |
| --- | --- | --- |
| [`代码审查报告.md`](代码审查报告.md) | 6 个项目（zjy2/5/6/7/8/9） | 2026-09-21 |
| **本报告** | **zjy13 + yuanma** | 2026-11-05 |

两份报告合并后，工作区 11 个目录中仍有 `zjy10`、`zjy11`、`zjy12` 未被代码审查覆盖
（`zjy12` 为纯设计文档 + 一个小 bench 脚本，`zjy10` 为规划文档，优先级较低）。

---

## 八、审查方法与局限

**方法**：

- 按模块拆成 5 个独立审计单元并行阅读，每个单元只负责一块，避免单次上下文过长导致遗漏
- 要求每条问题给出具体文件行号与关键代码片段作为证据
- 关键结论到 ESP-IDF 5.5.5 与 `esp_websocket_client` 源码交叉验证，不只凭注释推断

**局限**：

- 纯静态阅读，**未执行代码**，运行时行为（如竞态的触发概率）需实测确认
- 问题按静态可判定性排序，实际修复优先级还应结合真实使用场景
- `yuanma` 固件部分审计单元的输出在传输中被截断，本报告收录了该单元已返回的 5 条问题（H7–H10、M19），
  可能仍有未收录项
- **中危 8 项与低危 26 项没有逐条落盘**（见第三、四节开头），本报告只能作为「部分清单 + 类别线索」使用

---

## 九、修复状态

本报告不维护修复状态。逐项追踪见 [`代码审查修复台账.md`](代码审查修复台账.md)。
