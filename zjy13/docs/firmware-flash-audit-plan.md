# ESP32-S3 固件烧录前检查方案与问题清单

> 重新整理日期：2026-10-29。
> 本文依据本次会话已经读取的源码、构建配置和检查输出编写；重新写入文档不表示重新执行了构建或硬件验收。
> 本次操作仅更新此 Markdown 文件，不修改固件源码、构建配置、脚本、二进制或备份，不执行烧录、擦除和 eFuse 操作。

## 1. 当前结论

**现有固件不适合直接作为正式版本烧录。** 它已编译出 ESP32-S3 镜像，镜像校验和构建地址正常，但源码存在会阻断联网、录音和播放的缺陷。编译成功、Python 测试成功，都不能替代这些执行路径的检查。

原板载应用为 `ai_pet_badge`，含显示、动画与 IMU 功能。现有备份是完整 Flash 二进制镜像，**不是该应用的源码**。目前没有证据表明新语音固件已经与原显示/动画程序完成源码集成。

此前“固件源码全部完成”“与原固件结合完成”“协议完全兼容”的结论过于宽泛。当前更准确的状态是：**有可编译的开发版本，仍需修复关键逻辑并完成硬件验收。**

## 2. 检查对象与边界

目标硬件：Waveshare ESP32-S3-Touch-LCD-1.85B，ESP32-S3R8，16MB Flash，8MB PSRAM。

主要检查文件：

| 类别 | 文件或目录 |
|---|---|
| 主程序 | `firmware/main/main.c` |
| 网络与 WebSocket | `firmware/main/app_net.c`、`app_net.h` |
| 录音与播放 | `firmware/main/app_audio.c`、`app_audio.h` |
| 协议与 JSON | `firmware/main/app_protocol.c`、`app_protocol.h` |
| 状态机 | `firmware/main/app_state.c`、`app_state.h` |
| 板级参数与配置 | `firmware/main/app_board.h`、`app_config.h`、`Kconfig.projbuild` |
| 显示 | `firmware/main/app_ui.c`、`app_ui.h` |
| 构建与分区 | `firmware/CMakeLists.txt`、`firmware/main/CMakeLists.txt`、`firmware/partitions.csv` |
| 实际构建配置 | `firmware/sdkconfig`、`firmware/sdkconfig.defaults` |
| 烧录地址清单 | `firmware/build/flash_args`、`flasher_args.json` |
| 镜像 | `firmware/build/zjy13_voice.bin`、`bootloader/bootloader.bin`、`partition_table/partition-table.bin` |
| 烧录与恢复脚本 | `scripts/flash_firmware.ps1` |
| 服务端参照 | `server/protocol/messages.py`、`server/ws/server.py`、`server/ws/audio.py` |
| 测试 | `tests/test_firmware_protocol.py`、`tools/fw_protocol_equiv.py` |

本文区分三种结论：

- **已确认缺陷**：从实际源码或构建配置能够直接证明。
- **待核实风险**：存在不合理实现，需要驱动源码、官方 BSP 或硬件测试证明。
- **硬件未验收**：不能仅凭编译或模拟器判断成功。

## 3. 已有构建与备份证据

### 3.1 当前构建产物

此前检查的产物如下，后续修改源码后必须重新构建、重新核对，不能继续沿用这组结果。

| 产物 | 此前记录大小 | 当前构建烧录偏移 |
|---|---:|---:|
| `bootloader.bin` | 约 20.3 KiB | `0x0` |
| `partition-table.bin` | 3072 字节 | `0x8000` |
| `zjy13_voice.bin` | 200816 字节 | `0x10000` |

构建参数：ESP-IDF v5.5.5，芯片 ESP32-S3，DIO，80MHz，16MB Flash。

`esptool image-info` 此前显示 app checksum 与 validation hash 有效，项目名为 `zjy13_voice`，版本为 `f612c12-dirty`。这证明镜像格式有效，**不证明业务功能可运行**。

### 3.2 三份原固件镜像

位置：`backup/esp32/`。

- `esp32s3_full_16MB_20261028_092303.bin`
- `esp32s3_full_16MB_ORIGINAL_ai_pet_badge.bin`
- `esp32s3_full_16MB_20261029_rebackup.bin`

此前三份均为 16777216 字节，SHA256 均为：

```text
4CF96ED593C6B036342A0854AF4F66FBF91E55F6F372ADF163E37A68DB457908
```

这些是同一份 Flash 读取结果的文件副本，不是三次从硬件独立读取验证。它们位于同一磁盘，也不能代替另一介质的离线备份。此前仅对前 1MiB 做过再次读取比对；完整 16MB 恢复尚未实测。

## 4. 已确认问题与待核实风险

### 4.1 阻断：当前 app 没有可用 Wi-Fi 配置

实际 `firmware/sdkconfig` 记录：

```ini
CONFIG_APP_WIFI_SSID=""
CONFIG_APP_WIFI_PASSWORD=""
CONFIG_APP_SERVER_HOST="192.168.1.100"
CONFIG_APP_SERVER_PORT=8765
```

`main.c` 检查 SSID 为空后直接从 `app_main()` 返回。因此现有产物启动后不会进入正常联网和对话流程。

修复要求：配置真实 2.4GHz SSID、必要的密码、电脑局域网地址和端口，然后重新构建。开放网络允许密码为空，**SSID 为空才是当前明确的阻断原因**。

### 4.2 阻断：录音前没有发送 recording_start

`start_recording()` 当前只切换本地状态并创建 `record_task`；`record_task` 随后直接采集、封帧上传。`app_proto_build_recording_start()` 虽已定义，却未被主录音路径调用。

服务端必须先处理 `recording_start` 才会创建本轮录音缓冲和返回 `session_start`。正确顺序应为：

```text
握手完成
→ 用户请求开始录音
→ 发送 recording_start（音频格式）
→ 等待 session_start
→ 保存本轮 turn_id，计算 CRC32
→ 开始上传二进制音频块
→ 停止采集，确认最后一块发送结束
→ 发送 recording_stop
```

必须处理等待超时、重复开始、用户在等待期间松手、发送失败和断线。

### 4.3 高风险：启动前缺少可见的 NVS 初始化路径

已读的 `app_main()` 与 Wi-Fi 初始化代码没有看到 `nvs_flash_init()`。ESP-IDF 默认 Wi-Fi 配置通常使用 NVS，仅在 CMake 中声明 `nvs_flash` 依赖不会自动初始化它。

检查要求：核对最终 Wi-Fi/NVS 配置，明确初始化责任。不能把“启动失败就擦除整个 NVS”当成默认修复，因为原备份分区可能包含现有设备配置。

### 4.4 高风险：I2C 地址可能混用了 8 位和 7 位格式

板级头文件写有：

```c
#define APP_CODEC_ADDR 0x30
#define APP_ADC_ADDR   0x80
```

音频代码把这两个值直接传给 `I2C_ADDR_BIT_LEN_7` 的 `device_address`。

`0x80` 已超出 7 位地址范围，至少 ES7210 的传参不正确。ES8311 的 `0x30` 也必须核对它在原官方驱动中究竟表示 8 位写地址还是 7 位设备地址。**不能只看数值能编译就认为正确，也不能未经核实直接全部右移。**

修复要求：对照官方 BSP、codec 驱动和芯片资料，统一地址约定；在硬件上确认 ACK。设备句柄创建成功也不等于芯片已经响应。

### 4.5 高风险：codec 初始化表缺少可靠核验及错误处理

ES7210/ES8311 配置由手写寄存器序列组成。此前没有展示与官方驱动逐项比对的证据，不能把注释中的“来自官方 BSP”作为验证结果。

大量 `i2c_write_reg()` 返回值未处理，即使芯片不响应，函数仍可能打印“配置完成”并返回成功。

修复要求：优先复用对应板型的官方 BSP/`esp_codec_dev`；若保留寄存器实现，逐项记录来源，检查每次写入，必要时回读关键寄存器，并向上层传播失败。

### 4.6 高风险：I2S 共享控制器的模式和时钟方案未成立

`app_audio_init()` 在同一个 I2S 控制器上同时分配 TX/RX：

- TX：标准模式、24kHz、mono、16bit。
- RX：TDM 模式、16kHz、4 slots。

后续切换只调用 disable/enable 和 codec 配置，没有实际重建或重配置 I2S 时钟/slot。

这与源码注释声称的“切到 16k RX、切到 24k TX”不一致。共享控制器的双向模式与时钟约束，必须对照 ESP-IDF v5.5.5 驱动确认。

修复方向：采用经过 BSP 验证的统一时钟方案，或在半双工切换时正确释放、重建活动通道。选用 TDM 本身也不等于已证明 ES7210 的线路格式正确，需要与官方实际采集方式核对。

### 4.7 已确认缺口：当前没有保留原显示程序

当前 `main/CMakeLists.txt` 强制定义：

```cmake
CONFIG_APP_UI_LCD_ENABLE=0
```

所以 menuconfig 的 LCD 选项不能正常控制这个构建。已有的 LCD 启用分支也只是方块占位，没有完成中文排版与角色动画；触摸驱动、IMU 动画逻辑未接入。

新 app 会取代原 app 的运行代码。即使原动画素材的 Flash 字节仍在，也不会自动继续执行原显示程序。

**要同时保留旧显示和增加语音，必须取得匹配的原应用源码进行集成，或明确重新实现所需显示功能。二进制镜像不能直接当源码合并。**

### 4.8 已确认差异：新分区表不兼容原布局

| 分区 | 原备份布局 | 当前新布局 |
|---|---|---|
| nvs | `0x9000`，`0x4000` | `0x9000`，`0x6000` |
| otadata | `0xD000`，`0x2000` | 不存在 |
| phy_init | `0xF000`，`0x1000` | 相同 |
| factory | `0x20000`，`0x900000` | `0x10000`，`0x400000` |
| model | `0x920000`，`0x50C000` | 不存在 |
| storage | 不存在 | `0x410000`，`0x400000` |

新布局既没有原 `model` 映射，也改变了 NVS 大小。原 NVS 在新大小下是否能直接使用需要核实。

必须纠正此前“普通 flash 一定整片擦除”的说法：**普通烧录按写入区域擦写，不等于 erase-flash。** 当前完整构建烧录会替换 bootloader、分区表、app，并覆盖部分原 app 地址范围；未覆盖区域可能仍保留原字节，但这不表示旧程序或资源已被正确集成。`erase-flash` 才会擦除整片。

### 4.9 高风险：DMA 排空没有有效验证

当前 `app_audio_play_drain()` 调用：

```c
i2s_channel_write(s_tx, NULL, 0, NULL, timeout_ms);
```

这是零长度 write，不是已经证明有效的“等待 DMA 播放完成”接口。必须读取对应版本驱动实现，确定返回值和执行语义，并用 TX 完成事件或有依据的排空机制实现收尾。

当前 `finish_playback()` 忽略 drain 返回值，随后停止 TX、关闭功放并上报 `playback_done`，可能截断尾音。

### 4.10 高风险：网络事件处理直接进行阻塞播放

当前 WebSocket DATA 回调直接调用 `app_audio_play_write()`，可能阻塞到 2 秒；收尾、错误处理还会等待或延时。

这会阻塞网络事件任务，影响继续收包、ping/pong、取消响应和连接状态处理。更合理的结构是：接收回调验证并投递有界队列，独立播放任务负责 I2S；设计队列满时的错误或背压处理。

还应检查部分写入：当前 `app_audio_play_write()` 没有让上层知道实际 `written` 字节数，接收统计与实际播出量可能不一致。

### 4.11 已确认缺陷：Wi-Fi 断线没有重连调用

`WIFI_EVENT_STA_DISCONNECTED` 只清理连接状态和 IP，没有重新调用 `esp_wifi_connect()`；WS 重连任务又只等待 Wi-Fi ready。因此实际断网后可能永久停在等待状态。

需要实现有退避的 Wi-Fi 重连，并避免在事件回调内长时间阻塞。

### 4.12 已确认缺陷：所谓“重连”路径调用的是永久停止

心跳超时或 fatal 错误调用 `app_ws_stop()`，其实现把 `s_running=false`，会让重连任务退出并销毁客户端。当前没有相应自动重新启动调用。

因此“心跳超时后自动重连”的日志与实际行为不符。需要区分“重新连接”和“用户永久停止”。

### 4.13 已确认缺口：WebSocket 消息分片没有重组

文本事件只处理 `payload_offset==0` 的第一片，直接把它当完整 JSON；剩余片段被忽略。二进制事件也直接把收到的片段当完整协议帧。

必须根据实际组件事件提供的 message/frame 长度、偏移与 opcode 语义重组；设置最大缓冲和超时，拒绝异常分片。8192 字节 buffer 不能保证 64KiB 的 JSON 总是单次交付。

### 4.14 已确认缺口：轮次与帧序号校验不足

下行解析得到 `hdr.turn_short` 和 `hdr.seq`，但当前播放路径没有比较当前轮次、检查重复/错序。旧帧或重复帧可能被计入本轮并播放。

JSON 轮次消息也需要校验 `turn_id`，不能只按消息类型推进状态。取消/断线后必须清空当前轮次及队列，禁止迟到帧复活播放。

### 4.15 已确认缺陷：握手音频格式的嵌套解析不正确

服务端 envelope 的业务字段平铺，但 `audio_in` 和 `audio_out` 仍然是嵌套对象。当前通用查找首个 `sample_rate` 的方法可能读到 `audio_in.sample_rate=16000` 并误当作播放采样率。

正确做法是解析明确路径 `audio_out.sample_rate`，并验证 channels/bits/encoding。不能把“envelope 平铺”误解成“全部子对象都被展开”。

### 4.16 已确认缺口：唯一输入路径与 USB 接口不匹配

代码读 UART0，注释却把 UART0 说成原生 USB Serial/JTAG，这是错误的：它们是不同外设。此前板子连接使用的是原生 USB Serial/JTAG。

必须核对最终 console 配置，选择 USB Serial/JTAG 输入、标准 VFS 控制台，或明确接线的 UART0。触摸录音尚未实现，不能宣称用户可直接按屏幕说话。

## 5. 完整检查与验收步骤

### 步骤 A：冻结待审版本

- 记录源码 commit 和工作区改动。
- 记录 IDF、编译器、依赖锁定文件和实际 sdkconfig。
- 检查配置时不输出 Wi-Fi 密码、设备 token 或服务端密钥。
- 源码或配置发生变化后，之前镜像与测试结果均需重新关联。

通过标准：能准确回答“这个 bin 是由哪份源码和哪份配置生成”。

### 步骤 B：保护原固件

- 对三个备份计算长度和完整 SHA256。
- 将至少一份副本复制到另一介质，复制后再校验。
- 记录恢复命令、目标芯片和实际串口。
- 区分文件复制校验、硬件二次读取校验与真实恢复测试，不混称为全部完成。
- 备份可能含 Wi-Fi 凭据，不公开上传。

通过标准：原始镜像未被修改，恢复路径明确。恢复本身仍需单独授权和安排。

### 步骤 C：核对实际配置

- SSID 非空、网络为 2.4GHz；开放网络允许密码为空。
- 电脑 LAN IP 和端口正确；`127.0.0.1` 不能作为板子连接电脑的地址。
- 服务端监听 LAN 可达地址；Windows 防火墙已按实际环境设置。
- 设备 token 与服务端配置一致。
- 检查最终 Flash、PSRAM、console 配置，不只看 defaults。

通过标准：正式设备配置已经构建进本次镜像，且无敏感配置被公开记录。

### 步骤 D：静态审查执行路径

逐项审查启动、NVS、Wi-Fi、WS 握手、录音请求、session_start、音频传输、播放、取消、错误恢复与断线重连。

重点要求：

- 所有重要 API 返回值都处理。
- 不在网络事件回调执行长耗时 I2S 操作。
- 全部资源有明确所有者和释放路径。
- 跨任务共享状态有同步；`volatile` 不能代替锁或队列。
- 任何错误都能关闭功放、停止采集并恢复可预测状态。

通过标准：第 4 节阻断/高风险项均被修复或有可核验的技术解释。

### 步骤 E：真实构建与镜像核验

在正确 IDF 环境中执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$env:IDF_TOOLS_PATH = "D:\esp\.espressif"
$env:IDF_PATH = "D:\esp\esp-idf-5.5.5"
& "$env:IDF_PATH\export.ps1"
Set-Location D:\xiangmudaima\zjy13\firmware
idf.py reconfigure
idf.py build
idf.py size
idf.py partition-table
```

检查：

- 构建真实退出码为 0，没有被 PowerShell 管道隐藏。
- app、bootloader、partition table 来自同一次配置。
- 对照 `flasher_args.json`、分区二进制和 `partitions.csv`，核对地址、边界和大小。
- app 镜像目标芯片、checksum、validation hash 正常。
- app 大小小于 factory 分区。
- 使用 map 和 size 理解 IRAM/DIRAM 分布；不能只凭表中某个区域显示 100% 就断定整个芯片内存耗尽，也不能仅凭小 bin 判断运行内存充分。
- 生成最终交付文件的完整 SHA256 清单。

### 步骤 F：协议与行为测试

现有测试可作为基础：

```powershell
Set-Location D:\xiangmudaima\zjy13
.\.venv\Scripts\python.exe -m pytest tests/test_firmware_protocol.py -q
```

必须补充能执行实际 C 逻辑或能覆盖完整流程的验证：

- `recording_start` 必须真的发出，且音频上传晚于有效 session_start。
- 测试 CRC32、大小端、长度检查、JSON 转义/嵌套解析。
- 测试分片、截断、异常长度、旧轮次、重复帧与 seq 回绕。
- 测试超时、断线、录音取消、播放取消、设备未握手和任务创建失败。
- 测试部分播放写入与尾帧排空。

**现有测试的局限：** 多数静态测试只检查宏值或源码文本是否出现某函数；Python 等价脚本自行发送了 `recording_start`，固件主程序却没有发送。因此 Python 两轮成功不能证明实际 C 录音状态机正确。报告必须明确这一点。

### 步骤 G：评审烧录/恢复脚本

检查 `scripts/flash_firmware.ps1`：

- 从 workspace 根目录或其他目录启动都能正确解析路径。
- 目标端口来自实际枚举，不默认认为一定是 COM7。
- 缺产物、缺配置、构建失败、erase 失败立即终止。
- 恢复前校验长度、SHA256、芯片类型和目标端口。
- 参数说明与实际支持项一致。
- 不默认 erase-flash，不写 eFuse。
- 未验证设备时不自动烧录。
- 记录普通 app 写入、更新分区表、整片擦除、整片恢复的区别。

通过标准：脚本不会在前置失败后继续写 Flash，也不会误写其他串口或错误镜像。

### 步骤 H：上板后分阶段验收

在用户同意烧录且设备已接入后再执行：

1. 确认芯片身份、Flash 容量、安全启动/Flash 加密状态及实际端口。
2. 检查启动、NVS、PSRAM、console；不得循环重启。
3. 验证 Wi-Fi 连接、DHCP、断开后恢复。
4. 验证 WS 握手和心跳，检查音频格式协商。
5. 验证 recording_start/session_start，不上传音频前先确认 turn_id。
6. 验证真实麦克风：格式、采样率、通道含义、电平、ASR 文本。
7. 验证真实扬声器：24kHz、音量、尾音、失真与功放时序。
8. 验证半双工：录音时功放关闭，播放时停止录音，无自回采。
9. 验证取消、断线、服务端失败、坏帧后的恢复。
10. 至少十轮连续对话，并记录内部 heap、最大连续块、PSRAM、任务栈、错误次数；不能凭几秒内存不增长就宣称无泄漏。
11. 如需保留原显示/动画，单独验收其功能与语音并行运行时的性能。

通过标准：每阶段都有实际日志及可重复结果，上一阶段未过不继续扩大测试。

## 6. 建议修复顺序

| 顺序 | 内容 | 原因 |
|---|---|---|
| P0 | 明确原显示/动画保留方案和分区策略 | 当前尚未实现用户要求的原固件结合 |
| P0 | NVS 初始化、有效设备配置 | 否则不能可靠启动联网 |
| P0 | recording_start → session_start → 上传状态机 | 当前录音链路直接不符合服务端协议 |
| P0 | 7 位 I2C 地址、官方 codec 驱动、I2S 时钟/模式方案 | 直接决定真实录音播放能否工作 |
| P1 | USB/触摸输入、WS 分片与播放任务队列 | 避免无法操作、丢消息和阻塞 |
| P1 | 播放完成、取消、轮次/seq 校验 | 避免尾音截断、串轮和错误统计 |
| P1 | Wi-Fi/WS 超时重连 | 确保断线可恢复 |
| P1 | 烧录脚本前置检查、镜像清单和回滚校验 | 防止错误配置或产物被写入 |
| P2 | 实际板载联调和长期稳定性 | 静态/模拟测试不能替代硬件证据 |

## 7. 最终放行标准

只有同时满足以下条件，才建议把某一版产物标记为“可进行受控上板验证”：

- 原固件至少两份校验通过，原显示功能的保留/替换方案已明确。
- 有效 Wi-Fi/服务端配置已写入本次镜像。
- NVS、录音握手、I2C 地址、codec 和 I2S 关键问题已修复。
- bootloader、分区表、app 的地址与同一次构建对应。
- 行为测试覆盖了真实 C 路径，而不仅是宏值和 Python 模拟。
- 烧录与恢复脚本完成只读审查。
- 硬件未验证项明确记录，未把“受控试烧”称为“正式验收完成”。

**本文件重新写入时的状态：检查方案已整理；固件缺陷未在本次操作中修改，未执行新的构建、烧录或擦除。**
---

## 8. 2026-10-29 修复记录

本次已修改固件源码，并重新编译通过。以下为逐项结果。

### 8.1 已修复

| 原问题 | 修复内容 | 位置 |
|---|---|---|
| Wi-Fi SSID 为空导致启动即退出 | 保留阻断检查并改为显式报错；真实凭据仍需构建前配置 | `main.c` |
| 从未初始化 NVS | 新增 `nvs_flash_init()`，处理 `NO_FREE_PAGES` / `NEW_VERSION_FOUND` | `main.c` |
| I2C 地址使用 8 位写法 | 改为 7 位：ES7210 `0x40`、ES8311 `0x18`，并读写探测确认应答 | `app_board.h`、`app_audio.c` |
| 录音前不发 `recording_start` | 先发请求，等待 `session_start` 取得 `turn_id` 后再采集上传 | `main.c` |
| I2S 切换不改时钟 | 使用 `i2s_channel_reconfig_tdm_clock` / `reconfig_std_clock` 真正重配 | `app_audio.c` |
| codec 寄存器手写、忽略错误 | 每条写入检查返回值；ES8311 按采样率查分频表；配置失败向上传播 | `app_audio.c` |
| `play_drain` 用零长度写入 | 改为写入等深静音顶出 DMA 队列，并检查返回值 | `app_audio.c` |
| 播放写入不返回实写字节数 | `app_audio_play_write` 增加 `written` 出参，部分写入告警 | `app_audio.h/.c` |
| Wi-Fi 断线不重连 | `STA_DISCONNECTED` 中调用 `esp_wifi_connect()` | `app_net.c` |
| `app_ws_stop` 被当作重连使用 | 新增 `app_ws_restart()`；心跳超时与 fatal 错误改用它 | `app_net.c`、`main.c` |
| WebSocket 分片只处理首片 | 按 `payload_offset` / `payload_len` 重组文本与二进制，含上限与断线清理 | `app_net.c` |
| 下行帧不校验轮次/序号 | 比较 `turn_short` 丢弃旧轮帧；丢弃重复/乱序帧 | `main.c` |
| `server_hello` 取顶层 `sample_rate` | 新增嵌套对象提取，读 `audio_out` 内的字段 | `main.c`、`app_protocol.c` |
| 串口输入误认为 UART0＝USB | 优先安装 USB Serial/JTAG 驱动，失败再退回 UART0 | `main.c` |
| 录音缓冲每次任务内分配 | 改为启动时分配一次并复用，避免异常退出泄漏 | `main.c` |

### 8.2 本次构建结果

- app `zjy13_voice.bin`：215360 字节
- bootloader：20832 字节 @ `0x0`
- partition-table：3072 字节 @ `0x8000`
- flash 参数：DIO / 80MHz / 16MB
- `esptool image-info`：checksum `0xf8` valid，validation hash valid，ESP-IDF v5.5.5
- 测试：`.venv` 262 passed，`.venv-asr` 272 passed

### 8.3 仍未完成或未验证

- **Wi-Fi 凭据仍需在构建前配置**：当前 `sdkconfig` 中 SSID 仍为空，必须用 menuconfig 填写后重建。
- **本机没有无线网卡**：电脑仅有有线以太网，板子无法直接连到本机所在无线网络；需要外部 2.4GHz AP 且电脑与板子在同一网段。
- **触摸屏交互未实现**：当前仍只有 USB/UART 单字符命令。
- **LCD 显示仍被强制关闭**：`CONFIG_APP_UI_LCD_ENABLE=0` 保持不变，未做中文显示。
- **未保留原 `ai_pet_badge` 显示/动画应用**：分区布局未改，新 app 仍会替换原应用；原固件仅有二进制备份，无法合并源码。
- **所有音频硬件行为未上板验证**：I2C 应答、TDM 四通道、ES8311 24kHz 播放、功放时序均需实测。
- **`play_drain` 的静音填充方案未经硬件确认**：语义确定，但仍需实测尾音是否完整。

**结论：阻断级源码缺陷已修复且可编译；由于 Wi-Fi 未配置且硬件全部未验证，仍不应把本产物标记为已验收固件。**
