# ESP32-S3-Touch-LCD-1.85B 硬件资料核实

> 全部信息来自 **Waveshare 官方仓库**与 **docs.waveshare.com**，非推测。
> 仓库：<https://github.com/waveshareteam/ESP32-S3-Touch-LCD-1.85B>
> 文档：<https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85B>
> 核实日期：2026-10-26

---

## 一、结论：能找到，而且是完整的一手资料

答案是**能**，而且比预期更完整：

| 资料 | 位置 | 状态 |
| --- | --- | --- |
| **原理图 PDF** | `hardware/ESP32-S3-Touch-LCD-1.85B Rev1.1.pdf` | ✅ 在线可下载 |
| **官方 BSP 组件** | `Examples/ESP-IDF-V5.5.3/01_comprehensive_example/components/waveshare__esp32_s3_touch_lcd_1_85B` | ✅ 源码完整 |
| **ESP-IDF 示例** | `Examples/ESP-IDF-V5.5.3/` 共 8 个 | ✅ |
| **Arduino 示例** | `Examples/Arduino-V3.2.0/` | ✅ |
| **出厂固件** | `Firmware/ESP32-S3-Touch-LCD-1.85B-Factory.bin` | ✅ |
| 官方文档 | <https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85B> | ✅ |

---

## 二、音频硬件（官方文档确认）

| 芯片 | 作用 | 接口 | I2C 地址 |
| --- | --- | --- | --- |
| **ES7210** | 回声消除 / 双麦克风 ADC | I2C + I2S | 0x80 |
| **ES8311** | 音频编解码（DAC，扬声器） | I2C + I2S | 0x30 |

板载**双麦克风阵列** + **回声消除电路** + **内置扬声器**。

---

## 三、音频引脚定义（官方 BSP 源码）

来源：`include/bsp/esp32_s3_touch_lcd_1_85B.h`

```c
#define BSP_I2C_SCL           (GPIO_NUM_10)
#define BSP_I2C_SDA           (GPIO_NUM_11)
#define BSP_I2S_SCLK          (GPIO_NUM_48)   // BCLK
#define BSP_I2S_MCLK          (GPIO_NUM_2)    // MCLK
#define BSP_I2S_LCLK          (GPIO_NUM_38)   // LRCK / WS
#define BSP_I2S_DOUT          (GPIO_NUM_47)   // 到 ES8311（播放）
#define BSP_I2S_DSIN          (GPIO_NUM_39)   // 从 ES7210（录音）
#define BSP_POWER_AMP_IO       (GPIO_NUM_9)   // 功放使能
```

**I2C 总线共享**：ES7210、ES8311、触摸、IMU、RTC、电量计都在 GPIO10/11 上。

---

## 四、音频格式（关键）

### 4.1 I2S 硬件配置

来源：`esp32_s3_touch_lcd_1_85B.c` 第 73-77 行

```c
#define BSP_I2S_DUPLEX_MONO_CFG(_sample_rate)
    .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(_sample_rate),
    .slot_cfg = I2S_STD_PHILIP_SLOT_DEFAULT_CONFIG(
                    I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
```

| 项 | 值 |
| --- | --- |
| 位深 | **16 bit** |
| 单声道/立体声 | **MONO**（菲利普格式） |
| 默认采样率 | **22050 Hz**（第 656 行 `BSP_I2S_DUPLEX_MONO_CFG(22050)`） |

> ⚠️ **22050 是 BSP 的默认值，不是硬性限制**。
> `bsp_audio_init()` 接受 `i2s_config` 参数，可以传入自定义采样率。
> **实际采集与播放采样率需在固件中显式设置**，不能假设。

### 4.2 麦克风是 4 通道（重要）

来源：`04_esp_wakeword_det/main/wake_word_det/wake_word_drv.c`

```c
#define ADC_I2S_CHANNEL 4
```

ES7210 输出 **4 个通道**的原始数据：

```c
// 帧布局：buffer[4*i + 0..3]
// 重排为 3 通道供 AFE 使用：
buffer[3*i + 0] = buffer[4*i + 1];   // mic1
buffer[3*i + 1] = buffer[4*i + 3];   // mic2
buffer[3*i + 2] = ref;               // 参考信号（回声消除用）
```

输入格式字符串：

```c
static char* bsp_get_input_format(void) { return "RMNM"; }
```

`RMNM` = Reference + Mic + Noise(Mic2) + Mic

**这意味着**：

- ESP32 采集到的是 **4 声道交错**的 int16 数据
- 需要**降混**才能得到单声道，送给 ASR
- 第 0 通道是**参考信号**（扬声器回采），不是人声
- 第 1、3 通道是两个麦克风
- 第 2 通道用途不明确（可能是第三个麦克风或未使用）

**对 ASR 的影响**：

> V2 第五节要求"根据硬件实际采集格式在电脑端执行必要的重采样、声道转换"。
> 这里就是典型场景：**4 声道 → 1 声道**，而且不能简单取平均
> （参考通道会污染人声）。

---

## 五、对项目的影响

### 5.1 WebSocket 协议需要携带的格式信息

```json
{
  "type": "recording_start",
  "sample_rate": 22050,     // 待固件端确认
  "channels": 4,            // ES7210 原始通道数
  "bits": 16,
  "encoding": "pcm_s16le",
  "channel_layout": "RMNM"
}
```

**不能假设 16 kHz 单声道**。V2 明确指出：

> 第一版建议统一 ASR 输入为 16 kHz、16-bit signed PCM、单声道。
> **此为服务端 ASR 输入格式，不代表开发板麦克风必须原生以此格式采集。**

### 5.2 服务端需要做的转换

```
ESP32 采集：4ch / 22050 Hz / 16 bit（待确认）
        ↓ 服务端
        ├─ 取 mic1 + mic2 通道（丢弃 ref）
        ├─ 降混为单声道
        ├─ 重采样 22050 → 16000
        ↓
    ASR 输入：1ch / 16000 Hz / 16 bit
```

现有的 `asr/audio_utils.py` 已支持重采样和声道降混，但**需要扩展为支持多通道选择**（不能简单 mean，因为 ref 通道是噪声）。

### 5.3 TTS 播放格式

TTS 生成的是 **24000 Hz / 16 bit / 单声道**（CosyVoice3 输出）。

开发板播放需要：
- 重采样 24000 → 22050（或固件支持 24000）
- 16 bit 单声道（与 BSP 配置一致）

**这需要在服务端做，不能假设开发板能直接播放任意格式**（V2 第六节第 5 条）。

---

## 六、官方示例参考

| 示例 | 用途 | 对本项目价值 |
| --- | --- | --- |
| `01_comprehensive_example` | 综合示例 | **含完整 BSP，最有参考价值** |
| `02_lvgl_demo` | LVGL 界面 | 屏幕显示参考 |
| `03_mp3_player` | MP3 播放 | 音频输出参考 |
| `04_esp_wakeword_det` | 唤醒词识别 | **麦克风采集参考** |
| `05_esp-brookesia` | UI 框架 | 可选 |
| `06_qmi8658_test` | 六轴传感器 | 不需要 |
| `07_pcf85063_test` | RTC | 不需要 |
| `08_bq27220_test` | 电量计 | 不需要 |

---

## 七、待确认项

以下信息**尚未从资料中确认**，需要在真机或固件中验证：

| 项 | 为什么重要 | 如何确认 |
| --- | --- | --- |
| **实际采集采样率** | BSP 默认 22050，但示例可能改过 | 查 `01_comprehensive_example` 的 `bsp_audio_init` 调用 |
| 第 2 通道用途 | 影响降混逻辑 | 查原理图 PDF 或实测 |
| 扬声器实际能力 | 决定 TTS 输出格式 | 查原理图 + 实测 |
| 内存余量 | 决定音频缓冲大小 | 实测 `esp_get_free_heap_size()` |
| Wi-Fi 实测带宽 | 决定音频块大小 | 实测 |

---

## 八、如何自己核实

### 原理图

```
https://github.com/waveshareteam/ESP32-S3-Touch-LCD-1.85B/blob/main/hardware/ESP32-S3-Touch-LCD-1.85B%20Rev1.1.pdf
```

### 官方 BSP 源码

```
https://github.com/waveshareteam/ESP32-S3-Touch-LCD-1.85B/tree/main/Examples/ESP-IDF-V5.5.3/01_comprehensive_example/components/waveshare__esp32_s3_touch_lcd_1_85B
```

### 唤醒词示例（麦克风用法）

```
https://github.com/waveshareteam/ESP32-S3-Touch-LCD-1.85B/tree/main/Examples/ESP-IDF-V5.5.3/04_esp_wakeword_det
```

### 注意事项

- `www.waveshare.com` 会返回 **403**（反爬），需带浏览器 User-Agent
- `api.github.com` 在本机 TLS 偶发失败，用 `raw.githubusercontent.com` 更稳
- 官方组件仓库 `waveshareteam/Waveshare-ESP32-components` 的 `bsp/` 里
  **没有 `esp32_s3_touch_lcd_1_85B`**（只有 `1_83`、`1_69` 等），
  所以 1.85B 的 BSP 要从**产品专用仓库**取，不能从通用组件仓库取

---

## 九、一个容易踩的坑

本机 `zjy11/firmware/components/waveshare__esp32_s3_touch_lcd_1_85B/`
目录名看着是这块板子，但里面 `idf_component.yml` 写的是：

```yaml
description: Board Support Package (BSP) for Waveshare ESP32-S3-CAM-OVxxxx
repository_info:
  path: bsp/esp32_s3_cam_ovxxxx
```

**实际是 ESP32-S3-CAM 的组件，被错误地放进了 1.85B 的目录名里。**

如果照着它推断引脚，会得到完全错误的结论。**这也是为什么必须核对官方仓库。**
