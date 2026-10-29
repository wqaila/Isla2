#pragma once

/*
 * ESP32-S3-Touch-LCD-1.85B 板级硬件定义
 *
 * 板子：Waveshare ESP32-S3-Touch-LCD-1.85B
 *   - ESP32-S3R8：8MB PSRAM（Octal）/ 16MB Flash
 *   - 显示：ST77916 QSPI 360x360
 *   - 触摸：CST816S（I2C 0x15）
 *   - 音频：ES7210（4 通道 ADC）+ ES8311（DAC）+ 板载功放
 *
 * 引脚来源：微雪官方仓库
 *   Examples/ESP-IDF-V5.5.3/01_comprehensive_example/components/
 *   waveshare__esp32_s3_touch_lcd_1_85B/include/bsp/esp32_s3_touch_lcd_1_85B.h
 *
 * 与 zjy11 的 app_board.h 一致（那套已核对过官方资料）。
 *
 * 注意：PWR 键不支持自定义（长按 2s 开关机），BOOT 键在 GPIO0 是启动配置脚，
 * 都不能当聊天按键用。本项目用触摸屏交互。
 */

/* ===== 显示 ST77916（QSPI，360x360，RGB565）===== */
#define APP_LCD_CS          21
#define APP_LCD_PCLK        40
#define APP_LCD_DATA0       46
#define APP_LCD_DATA1       45
#define APP_LCD_DATA2       42
#define APP_LCD_DATA3       41
#define APP_LCD_RST         3
#define APP_LCD_BL          5      /* 背光，PWM 可调 */
#define APP_LCD_H_RES       360
#define APP_LCD_V_RES       360

/* ===== 触摸 CST816S ===== */
#define APP_TP_I2C_SCL      10
#define APP_TP_I2C_SDA      11
#define APP_TP_RST          1
#define APP_TP_INT          4
#define APP_TP_I2C_ADDR     0x15

/* ===== 共享 I2C 总线（触摸 / IMU / 音频 / 电量 / RTC 共用）===== */
#define APP_I2C_SCL         10
#define APP_I2C_SDA         11
#define APP_IMU_ADDR        0x6B   /* QMI8658 */
#define APP_FUEL_ADDR       0x55   /* BQ27220 */
#define APP_RTC_ADDR        0x51   /* PCF85063 */
#define APP_RTC_INT         6

/* ===== 音频（I2S 全双工）===== */
#define APP_AUDIO_MCLK      2
#define APP_AUDIO_BCLK      48
#define APP_AUDIO_LRCK      38
#define APP_AUDIO_DOUT      47     /* 到 ES8311（播放）*/
#define APP_AUDIO_DIN       39     /* 从 ES7210（录音）*/
#define APP_AUDIO_PA        9      /* 功放使能 */

/*
 * I2C 设备地址必须是 **7 位**形式，因为驱动用 I2C_ADDR_BIT_LEN_7。
 *
 * 来源：官方组件头文件（D:\xiangmudaima\zjy11 .../libraries/es7210/es7210.h、
 * es8311/es8311.h），不是推算：
 *   ES7210：AD0/AD1 接地时 7 位地址 = 0x40（8 位写法才是 0x80）
 *   ES8311：CE 低 = 0x18，CE 高 = 0x19（8 位写法才是 0x30/0x32）
 *
 * 踩坑记录：最初写成 0x80 / 0x30（8 位写法），直接传给 7 位接口后：
 *   0x80 超出 7 位上限 0x7F，参数非法；0x30 会寻址到别的器件。
 * 两个 codec 都不会应答，录音与播放全部失效。
 */
#define APP_CODEC_ADDR      0x18   /* ES8311，7 位地址（CE 接低） */
#define APP_ADC_ADDR        0x40   /* ES7210，7 位地址（AD0/AD1 接地） */

/*
 * ES7210 输出 4 通道交错，布局 "RMNM"：
 *   ch0 = Reference（扬声器回采，**不是人声**）
 *   ch1 = Mic1（人声）
 *   ch2 = 未知/降噪参考
 *   ch3 = Mic2（人声）
 *
 * 实测教训：若对 4 通道直接取平均，电平会被回声参考拉高 3.5 倍，
 * 识别质量明显下降。服务端已按此布局丢弃 ch0，固件只需原样上传。
 */
#define APP_MIC_CHANNELS    4
#define APP_MIC_LAYOUT      "RMNM"

/* ===== 音频采样率 =====
 *
 * 采集：16000 Hz —— ASR 的标准输入，避免服务端重采样。
 * 播放：24000 Hz —— CosyVoice3 输出格式，直接播不重采样。
 *
 * 重要约束（改实现前必须理解）：
 *   ES7210 与 ES8311 共用同一条 I2S 控制器和同一对 MCLK/BCLK/LRCK 时钟。
 *   因此**两者不能同时以不同采样率工作**。官方 BSP 让两个方向都跑 22050
 *   正是这个原因。
 *
 * 本固件因此采用严格的半双工：录音时整条 I2S 是 16k，播放时整条是 24k，
 * 切换时用 i2s_channel_reconfig_*_clock() 真正重配时钟，
 * 而不是只改 codec 寄存器。
 */
#define APP_MIC_SAMPLE_RATE  16000
#define APP_SPK_SAMPLE_RATE  24000

/* ===== MCLK 倍数 =====
 * ES8311 的采样率分频表按 mclk = sample_rate * mclk_div 查表。
 * 取值 256 是官方示例常用配置，16k/24k 都在该表覆盖范围内。
 */
#define APP_AUDIO_MCLK_DIV   256

/* ===== 音频块时长 =====
 *
 * 协议推荐 20~60ms。取 40ms 折中：
 *   16000 x 4ch x 16bit x 40ms = 5120 字节/块
 * 既控制延迟，也避免 ESP32 内存碎片。
 */
#define APP_AUDIO_CHUNK_MS   40
