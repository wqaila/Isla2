# ESP32-S3 固件

> 阶段五交付。日期：2026-10-29
> 状态：**代码完成并编译通过（196 KB），协议层已用等价字节验证通过服务端**。
> **尚未烧录到真实硬件**，音频链路未在实物上验证。

---

## 一、职责边界（很重要）

板子**不做任何推理**：

| 环节 | 在哪做 |
| --- | --- |
| 录音上传 | 板子（I2S / ES7210） |
| ASR | 电脑（FunASR） |
| LLM | 云端（DeepSeek） |
| TTS | 电脑（CosyVoice3 / WSL2） |
| 播放下行音频 | 板子（I2S / ES8311） |
| 屏幕显示 | 板子 |

所以固件很轻：**196 KB**（原厂 `ai_pet_badge` 是 7.32 MB，因为它带 6MB 动画资源）。

---

## 二、一次对话的完整数据流

```
按下触摸/串口 R
   │
   ├─ recording_start (JSON) ──────────────► 服务端
   │                                        │
   ├─ audio_chunk × N (二进制帧) ──────────► 录音中持续上传
   │     每块 5120 字节（40ms @16kHz 4ch）
   │
   └─ recording_stop (JSON) ───────────────► 服务端开始处理
                                            │
   ◄──────────────── asr_final (JSON) ────── 识别文本，显示
   ◄──────────────── llm_delta × N ───────── 流式回复，逐段显示
   ◄──────────────── llm_final
   ◄──────────────── playback_start ──────── 含总字节数/帧数
   ◄──────────────── tts_audio × N ───────── 边收边播（不攒整段）
   │
   ├─ playback_done (JSON) ────────────────► 上报实收字节数
   │     ⚠ 必须在 DMA 播完后才发
   │
   ◄──────────────── turn_done ───────────── 回到 IDLE
```

---

## 三、目录结构

```
firmware/
├─ CMakeLists.txt            顶层工程
├─ partitions.csv            分区表（4MB app + 4MB storage）
├─ sdkconfig.defaults        默认配置（16MB Flash / Octal PSRAM）
└─ main/
   ├─ CMakeLists.txt
   ├─ idf_component.yml      依赖 esp_websocket_client
   ├─ Kconfig.projbuild      menuconfig 里的 "zjy13 语音项目"
   ├─ main.c                 入口、消息分发、录音/播放任务
   ├─ app_board.h            板级引脚（官方 BSP 核实）
   ├─ app_config.h           协议常量（与服务端逐项对齐）
   ├─ app_protocol.h/.c      二进制帧编解码 + 轻量 JSON 构造/解析
   ├─ app_net.h/.c           Wi-Fi + WebSocket 客户端（含退避重连）
   ├─ app_audio.h/.c         I2S 录音/播放（半双工分时复用）
   ├─ app_ui.h/.c            屏幕显示（可关闭，默认关闭）
   └─ app_state.h/.c         状态机（与服务端 states.py 对齐）
```

---

## 四、关键技术决策

### 4.1 音频必须半双工

板载麦克风与扬声器距离很近。若边录边放，扬声器输出会被麦克风采回去
（回声参考通道 ch0 就是为此设计的），结果是**啸叫**。

所以 `app_audio.c` 里：

- `record_start()` → 先关功放（GPIO9 拉低），再切 I2S 到 16k RX
- `play_start()` → 先停录音，再切 I2S 到 24k TX，最后开功放

这也是 V2 第九节的硬性要求。

### 4.2 采样率：录音 16k / 播放 24k

| 方向 | 采样率 | 理由 |
| --- | --- | --- |
| 录音 | **16000 Hz** | ASR 标准输入，服务端不用重采样 |
| 播放 | **24000 Hz** | CosyVoice3 原生输出，不用重采样 |

两条都避免了额外的重采样损失。代价是 I2S 需要重新配置（见 4.1）。

### 4.3 4 通道必须用 TDM 模式

ES7210 输出 **4 通道交错**（布局 `RMNM`），I2S 标准模式只支持 MONO/STEREO。
用 STEREO 只能拿到 2 通道，**无法区分 ch0 是回声参考**，服务端的降混逻辑就会失效。

因此 RX 用 `i2s_channel_init_tdm_mode()`，4 个 slot 全开。

### 4.4 固件不做降混

固件**原样上传 4 通道原始数据**，降混留给服务端（`server/ws/audio.py`）。

理由：服务端的降混策略可能调整（比如发现 ch2 其实有用），这样改服务端就够了，不用重烧固件。

### 4.5 playback_done 必须等 DMA 播完

这是实测踩过的坑：把"写入完成"当成"播放完成"，会导致服务端在音频还没播完时
就开下一轮 → **串轮**。

`main.c` 里 `finish_playback()` 先调 `app_audio_play_drain()` 再上报。
有测试锁定这个顺序（`test_playback_waits_for_drain`）。

### 4.6 重连用退避，但重置在连上时

`app_net.c` 的重连任务：1s → 2s → 4s → 8s → 8s（封顶）。
**连上后立刻重置为 1s**，否则一次长时间断网后，恢复连接也要等 8 秒。

---

## 五、编译

```powershell
# 环境（注意用 5.5.5，不是 D:\esp\esp-idf 那个指向旧环境的）
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$env:IDF_TOOLS_PATH = "D:\esp\.espressif"
& "D:\esp\esp-idf-5.5.5\export.ps1"

cd D:\xiangmudaima\zjy13\firmware
idf.py set-target esp32s3
idf.py build
```

实测产物：

| 文件 | 大小 |
| --- | --- |
| `build/zjy13_voice.bin` | **196.1 KB** |
| `build/bootloader/bootloader.bin` | 20.3 KB |
| `build/partition_table/partition-table.bin` | 3.0 KB |

内存占用：DRAM 约 13 KB / IRAM 约 62 KB / Flash 约 82 KB。

---

## 六、配置（烧录前必须做）

```powershell
idf.py menuconfig
```

进 `zjy13 语音项目`：

| 配置项 | 说明 |
| --- | --- |
| `APP_WIFI_SSID` | **必须 2.4GHz**。配 5GHz 会 reason 201 |
| `APP_WIFI_PASSWORD` | 开放网络留空 |
| `APP_SERVER_HOST` | 电脑的局域网 IP |
| `APP_SERVER_PORT` | 默认 8765 |
| `APP_DEVICE_ID` | 设备标识，服务端日志用 |
| `APP_DEVICE_TOKEN` | 服务端用 `--token` 时才需要 |

> ⚠ **服务端必须用 `--host 0.0.0.0` 启动**。
> 默认的 `127.0.0.1` 只有本机可访问，板子连不上。

---

## 七、烧录

```powershell
# 编译并烧录
.\scripts\flash_firmware.ps1 -Port COM7 -Build

# 只烧（已编译过）
.\scripts\flash_firmware.ps1 -Port COM7

# 擦除后烧（干净，会丢 Wi-Fi 凭据）
.\scripts\flash_firmware.ps1 -Port COM7 -Erase

# 出问题了刷回原厂固件
.\scripts\flash_firmware.ps1 -Restore
```

### 分区表变化（重要）

| 项 | 原厂 `ai_pet_badge` | 本固件 |
| --- | --- | --- |
| factory | 0x20000，9216 KB | **0x10000，4096 KB** |
| model | 0x920000，5168 KB | 无 |
| storage | 无 | 0x410000，4096 KB |
| OTA 双槽 | 无 | 无 |

**烧录会整片覆盖原固件**，包括那套动画系统。因为应用分区只能放一个程序。

原固件已完整备份（16MB，SHA256 已验证，见下），`-Restore` 可还原。

---

## 八、备份状态

| 文件 | SHA256 | 说明 |
| --- | --- | --- |
| `backup/esp32/esp32s3_full_16MB_20261028_092303.bin` | `4CF96ED5...457908` | 原始备份 |
| `backup/esp32/esp32s3_full_16MB_ORIGINAL_ai_pet_badge.bin` | 同上 | 别名副本 |
| `backup/esp32/esp32s3_full_16MB_20261029_rebackup.bin` | 同上 | 烧录前二次备份 |

三份 SHA256 完全一致。

> ⚠ 备份**内含原固件的 Wi-Fi 凭据**，不要上传到公开仓库。
> `.gitignore` 已排除 `backup/esp32/*.bin`，但注意别手动提交。

---

## 九、验证方式（未烧板子也能验）

### 9.1 协议常量一致性（静态）

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_firmware_protocol.py -q
```

**54 个测试**，从 C 源码里读出宏值，逐个比对 Python 协议定义：

- 二进制帧头 12 字节 + magic 0xA5
- 18 个消息类型字符串
- 14 个错误码
- 音频格式（16k / 4ch / RMNM）
- 分块 40ms → 5120 字节，在协议推荐区间内
- 半双工逻辑（录音关功放、播放停录音）
- `playback_done` 在 drain 之后
- 分区表不超 16MB

### 9.2 字节级等价验证（需服务端运行）

```powershell
# 启动服务端
.\.venv-asr\Scripts\python.exe -m server.ws.server --port 8799

# 用固件的 C 逻辑（手写小端序列化 + 自实现 CRC32）生成字节
.\.venv-asr\Scripts\python.exe -m tools.fw_protocol_equiv --url ws://127.0.0.1:8799 --rounds 2
```

实测结果：

```
握手 OK  服务端=ai-role
session_start turn_id=439e92b3d329-t136 short=CFDC47D8
已上传 139 块 / 709976 字节
ASR: 欢迎大家来体验达摩院推出的语音识别模型
LLM: 哎呀，舰长这是在给人家介绍新朋友吗？语音识别模型呀……听起来好厉害的样子呢！✨
playback_start 预期 211 帧 / 864000 字节
turn_done reason=playback_done
音频校验: 收到 211/211 帧, 864000/864000 字节 → 一致

第 2 轮同样通过
结论: 固件协议实现与服务端完全兼容
```

**这一步很重要**：它用的不是 Python 的 `struct`/`zlib`，而是严格复刻固件 C 代码的
手写字节序和 CRC32 算法。所以"固件发的字节能被服务端正确理解"已经被证明了。

### 9.3 CRC32 一致性（单独确认）

```
'abc123-t1'  py=59C0712D fw=59C0712D  OK
'turn-0001'  py=857676E6 fw=857676E6  OK
'小满-1'      py=A5288BA7 fw=A5288BA7  OK
```

`turn_id_short` 用于二进制帧头匹配轮次，两边算法不一致会导致**静默丢弃所有音频**。

---

## 十、串口命令（联调用）

115200 8N1，单字符：

| 键 | 作用 |
| --- | --- |
| `R` | 开始录音 |
| `S` | 停止录音（触发识别） |
| `C` | 取消当前轮 |
| `P` | 打印状态（状态机/连接/帧数） |

> 触摸屏是主交互，串口命令是备用通道，两者都走同一个状态机。

---

## 十一、未验证项（如实标注）

### 硬件相关（全部未验证）

- [ ] **真实烧录**：代码只编译过，没上板
- [ ] **I2S 录音**：ES7210 寄存器序列来自官方 BSP，但**未实测**
- [ ] **I2S 播放**：ES8311 24kHz 能否直接播（BSP 默认 22050）
- [ ] **4 通道 TDM 实采**：能否真拿到 4 路，通道顺序是否如 `RMNM`
- [ ] **半双工切换**：I2S 重配置是否可靠，切换时有无爆音
- [ ] **功放 GPIO9**：时序是否正确
- [ ] **麦克风音质 / 扬声器音量 / 回声**
- [ ] **触摸交互**：触摸驱动未接入（当前只有串口命令）
- [ ] **LCD 显示**：默认关闭（`CONFIG_APP_UI_LCD_ENABLE=n`）

### 网络相关

- [ ] 真实 Wi-Fi 下的丢包/乱序/重连
- [ ] 长时间运行稳定性
- [ ] 并发多设备

### 已知限制

1. **没有 OTA**：升级需 USB 重烧（分区表无 ota_0/ota_1）
2. **明文 ws://**：无 TLS
3. **LCD 默认关闭**：开起来需要 ST77916 驱动组件 + 中文字库
4. **触摸未接入**：目前只有串口命令触发录音

---

## 十二、下一步（烧录后的调试顺序）

建议按这个顺序，每步单独确认，不要一次全上：

**第 1 步：只验网络**
```powershell
# 板子串口应看到
Wi-Fi 已连接，IP=192.168.x.x
WebSocket 已连接
```
若卡在 Wi-Fi，看 `reason=201` 说明 SSID/2.4GHz 问题；若连上但 websocket 失败，
检查服务端是否用了 `--host 0.0.0.0`、防火墙是否放行 8765。

**第 2 步：验文字链路（不碰音频）**
串口发 `R` 再发 `S`。即使麦克风有问题，ASR 会返回 `asr_empty`，
但**至少能确认 recording_start/stop 和 JSON 收发正常**。

**第 3 步：验录音**
录一段真话，看服务端日志里 ASR 返回什么。若返回空或乱码：
- 检查 I2S TDM 是否真收到 4 通道（日志里 `fmt=16000 Hz / 16 bit / 4 ch`）
- 用 `P` 命令看上传字节数是否合理（40ms 一块 = 5120 字节）

**第 4 步：验播放**
收到 `playback_start` 后应能听到声音。若无声：
- 确认功放 GPIO9 拉高
- 确认 ES8311 配置为 24kHz（BSP 默认 22050）

**第 5 步：验半双工**
录完立刻播，确认不啸叫。
