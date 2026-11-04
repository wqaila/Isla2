# zjy13 / yuanma 补充代码审查报告（2026-11-05）

> 背景：原 [`代码审查报告.md`](代码审查报告.md) 写于 2026-09-21，只覆盖当时 6 个项目，
> **未覆盖 `zjy13` 与 `yuanma`**。本报告补齐这两个项目。
>
> 审查方式：按模块拆成 5 个独立审计单元并行静态阅读，
> 逐行阅读 Python 与 C 源码，关键结论到 ESP-IDF 5.5.5 与 `esp_websocket_client` 源码交叉验证。
>
> 结论：**共 63 项问题，其中高危 10 项、中危 27 项、低危 26 项**。
> 另有 3 项跨文件核对后判定"不是问题"，见文末。

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

---

## 三、中危问题（27 项摘要）

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

---

## 四、低危问题（26 项摘要）

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
