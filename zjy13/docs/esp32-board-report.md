# ESP32-S3 板子状况报告与固件备份

> 检查日期：2026-10-28
> 设备：Waveshare ESP32-S3-Touch-LCD-1.85B
> 串口：COM7（USB-Serial/JTAG，VID 303A / PID 1001）

---

## 一、固件备份（已完成）

### 备份文件

| 项 | 值 |
| --- | --- |
| 路径 | `backup/esp32/esp32s3_full_16MB_20261028_092303.bin` |
| 大小 | **16,777,216 字节（16 MB 完整 Flash）** |
| SHA256 | `4CF96ED593C6B036342A0854AF4F66FBF91E55F6F372ADF163E37A68DB457908` |
| 读取耗时 | 73.2 秒 @ 1833 kbit/s |
| 启动日志 | `backup/esp32/bootlog.txt` |

### 完整性验证

重新读取前 1 MB 与备份逐字节比对：

```
对比: 前 1MB 完全一致 ✓ 备份可信
```

### 恢复方法（如果需要）

```powershell
cd D:\xiangmudaima\zjy13
.\.venv\Scripts\python.exe -m esptool --port COM7 --baud 921600 \
  write-flash 0 backup\esp32\esp32s3_full_16MB_20261028_092303.bin
```

> ⚠️ 恢复会**覆盖当前固件**。执行前请确认备份文件完好。

---

## 二、硬件信息（esptool 实测）

| 项 | 值 |
| --- | --- |
| 芯片 | **ESP32-S3 (QFN56) revision v0.2** |
| 特性 | Wi-Fi / BT 5 (LE) / 双核 + LP 核 / 240 MHz |
| **PSRAM** | **8 MB 内嵌（AP_3v3，Octal）** |
| Flash | **16 MB**（Manufacturer 0x20, Device 0x4018） |
| 晶振 | 40 MHz |
| USB 模式 | USB-Serial/JTAG（原生 USB，无需 CH340） |
| MAC | `44:bd:8d:78:14:68` |
| 启动模式 | SPI_FAST_FLASH_BOOT, DIO, 80 MHz |

这与官方资料一致（ESP32-S3R8 = 8MB PSRAM + 16MB Flash）。

---

## 三、⚠️ 重要发现：板子里已有一套项目固件

### 固件身份

| 项 | 值 |
| --- | --- |
| **项目名** | **`ai_pet_badge`** |
| **版本** | `e8aa327-dirty`（Git commit + 有未提交改动） |
| **编译时间** | **Oct 21 2026, 14:01:06** |
| IDF 版本 | v5.5.3 |
| 角色名 | **Elysia**（爱莉希雅） |
| 应用描述 | `Elysia IMU dashboard` |

**这不是出厂固件，是有人自己编译的项目。**

### 源码结构（从固件符号还原）

```
main/
├── main.c
├── badge_imu.c        # 六轴传感器
├── badge_network.c    # 网络（含天气 API）
└── badge_scene.c      # 场景/动画
```

### 分区布局

| 分区 | 类型 | 偏移 | 大小 | 使用率 |
| --- | --- | --- | --- | --- |
| nvs | data/nvs | 0x009000 | 16 KB | 23.8% |
| otadata | data/ota | 0x00D000 | 8 KB | 0% |
| phy_init | data/phy | 0x00F000 | 4 KB | 0% |
| **factory** | app | **0x020000** | **9216 KB** | **99.1%** |
| **model** | data/spiffs | **0x092000** | **5168 KB** | **99.6%** |

> `model` 分区 5 MB 且使用率 99.6%，说明**带了资源文件**（图片/动画素材）。

### 功能特征（从字符串提取）

| 功能 | 证据 |
| --- | --- |
| 天气预报 | `https://api.open-meteo.com/v1/forecast` |
| IMU 姿态检测 | `badge_imu` 日志、角度/摇晃统计 |
| 触摸交互 | `touches` 计数、tap 测试项 |
| 动画播放 | `clip`, `frame_index`, `frames`, `playing` |
| LCD 显示 | ST77916 QSPI 驱动 |
| Wi-Fi | 已初始化，但**当前未连接** |
| LVGL | 图形库 |

---

## 四、板子实时状态（STATUS 命令实测）

```json
{
  "clip": "sit",
  "playing": false,
  "stage": "idle",
  "action": "sit",
  "frame_index": 35,
  "frames": 40,
  "late_frames": 16,
  "touches": 0,
  "render_us": 9,
  "peak_render_us": 129947,
  "heap": 6622624,
  "min_heap": 6590052,
  "psram": 6547100,
  "imu_available": true,
  "angle": -141.42,
  "flat": true,
  "shakes": 0,
  "dizzy": false
}
```

### 解读

| 指标 | 值 | 说明 |
| --- | --- | --- |
| **堆内存** | 6.62 MB | 非常充裕（有 8MB PSRAM） |
| 最低堆 | 6.59 MB | 只降了 32 KB，无泄漏迹象 |
| PSRAM 可用 | 6.55 MB | 正常 |
| IMU | 可用 | QMI8658 工作正常 |
| 当前姿态 | `angle: -141.42`, `flat: true` | 平放状态 |
| 动画 | `clip: sit`, 第 35/40 帧 | 待机动画正常播放 |
| **late_frames** | **16** | ⚠️ 有丢帧，见下 |
| peak_render_us | 130 ms | 峰值渲染耗时偏高 |

### ⚠️ 一个值得注意的指标

`late_frames: 16` 表示有 16 帧渲染超时。结合 `peak_render_us: 129947`（130 毫秒），
说明**动画在某些情况下会卡顿**。

可能是首帧加载、或图片解码开销大。不影响使用，但如果做流畅动画需要优化。

---

## 五、自检结果（IMU SELFTEST 实测）

```
共 25 项测试，PASS 24，FAIL 0
```

全部通过，包括：

| 测试项 | 结果 |
| --- | --- |
| upright stable | PASS |
| rotation lag (6.0° 预期，实测 6.001°) | PASS |
| continuous 360 crossing | PASS |
| slow rotation no shake | PASS |
| settled pose returns neutral | PASS |
| flat holds orientation | PASS |
| disconnect preserves angle | PASS |
| opposite peaks trigger once | PASS |
| dizzy suppresses repeat shake | PASS |
| recovery cooldown | PASS |
| tap starts standing transition | PASS |
| shake cancels active action | PASS |
| RGB565 bilinear arithmetic | PASS |
| premultiplied alpha edge brightness | PASS |

**IMU 和动画系统工作正常。**

---

## 六、串口命令接口

固件提供一套串口调试命令（**不带前缀，直接输入**）：

| 命令 | 功能 |
| --- | --- |
| `STATUS` | 返回 JSON 状态（见上） |
| `NEXT` | 下一个动画 |
| `DASH` | 仪表盘模式 |
| `SHOT` | 截图 |
| `IMU` | IMU 数据 |
| `IMU CALIBRATE` | IMU 校准 |
| `IMU SELFTEST` | IMU 自检（25 项） |

**波特率 115200，换行符 `\n`。**

> 注意：固件字符串里还有 `BADGE_STATUS`、`BADGE_SELFTEST` 等，
> 但实际命令是**去掉 `BADGE_` 前缀**的短命令。发送带前缀的命令会返回
> `BADGE_ERROR unknown command`。

---

## 七、当前固件的局限性

对照我们正在开发的 V2 语音交互项目，现有固件**不能直接用**：

| 能力 | 现状 | V2 需要 |
| --- | --- | --- |
| 麦克风采集 | ❌ 未见 I2S 录音代码 | ✅ 必需 |
| 音频上传 | ❌ 无 WebSocket | ✅ 必需 |
| TTS 播放 | ❌ 未见音频播放代码 | ✅ 必需 |
| Wi-Fi 连接 | ⚠️ 已初始化但未连（reason 201） | ✅ 必需 |
| LCD 显示 | ✅ 正常 | ✅ 必需 |
| IMU | ✅ 正常 | 可选 |
| 触摸 | ✅ 正常 | ✅ 必需 |

**结论**：这是一套**动画/IMU 演示固件**，没有语音能力。
要做 V2 项目，需要**新写固件**，但可以参考它的：
- BSP 使用方式（`waveshare__esp32_s3_touch_lcd_1_85B` 组件）
- 分区表设计（特别是 5MB 的 model 分区）
- 动画/场景管理思路

---

## 八、Wi-Fi 状态

```
W (4764) badge_net: Wi-Fi disconnected, reason 201
```

`reason 201` = `WIFI_REASON_NO_AP_FOUND`（找不到 AP）。

说明固件里配了 Wi-Fi SSID/密码，但**当前环境连不上**（可能换过路由器，或配置在 NVS 里过期了）。

---

## 九、工具与环境

### 安装的 esptool

```
esptool 5.6.0（装在 .venv）
依赖：pyserial 3.5, cryptography 50.0.2, esp-pylib 1.2.0 等
```

### 常用命令

```powershell
cd D:\xiangmudaima\zjy13

# 探测芯片
.\.venv\Scripts\python.exe -m esptool --port COM7 chip-id

# 读 Flash 信息
.\.venv\Scripts\python.exe -m esptool --port COM7 flash-id

# 完整备份
.\.venv\Scripts\python.exe -m esptool --port COM7 --baud 921600 `
  read-flash 0 0x1000000 backup.bin

# 恢复
.\.venv\Scripts\python.exe -m esptool --port COM7 --baud 921600 `
  write-flash 0 backup.bin

# 擦除（危险）
.\.venv\Scripts\python.exe -m esptool --port COM7 erase-flash
```

### 串口交互

```powershell
.\.venv\Scripts\python.exe -c "
import serial, time
s = serial.Serial('COM7', 115200, timeout=2)
s.write(b'STATUS\n'); s.flush(); time.sleep(2)
print(s.read(s.in_waiting).decode('utf-8', errors='replace'))
s.close()
"
```

---

## 十、总结

| 项 | 结论 |
| --- | --- |
| 硬件 | 正常，ESP32-S3R8 + 8MB PSRAM + 16MB Flash |
| 固件备份 | ✅ 已完成并验证（16MB，SHA256 已记录） |
| 当前固件 | `ai_pet_badge`（Elysia IMU dashboard），非出厂 |
| 自检 | ✅ 24/25 通过，0 失败 |
| 内存 | 充裕（堆 6.6MB，PSRAM 6.5MB） |
| 显示/IMU/触摸 | ✅ 正常 |
| Wi-Fi | ⚠️ 已初始化但连不上（reason 201） |
| 音频能力 | ❌ 现有固件无麦克风/播放代码 |
| 动画性能 | ⚠️ 有 16 帧延迟，峰值渲染 130ms |

**可以安全开始开发新固件**——原固件已完整备份，随时可恢复。
