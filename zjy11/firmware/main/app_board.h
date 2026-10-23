#pragma once

/*
 * ESP32-S3-Touch-LCD-1.85B 板级引脚定义。
 *
 * 来源：微雪官方产品文档外设速查表（docs.waveshare.net）。
 * 用途仅限「核对官方示例」，不要用这些宏手写 QSPI/I2S 时序 ——
 * 驱动请直接复用与 B 型匹配的官方示例（BSP / LVGL 示例）。
 *
 * 第一版文字版只用：显示 + Wi-Fi（触摸可选）。
 * AUDIO_* 保留给 V2，第一版不要初始化，避免占用 I2S 与功放。
 */

/* ===== 显示 ST77916（QSPI，360x360，RGB565）===== */
#define APP_LCD_CS        21
#define APP_LCD_PCLK      40
#define APP_LCD_DATA0     46
#define APP_LCD_DATA1     45
#define APP_LCD_DATA2     42
#define APP_LCD_DATA3     41
#define APP_LCD_RST       3
#define APP_LCD_BL        5     /* 背光，PWM 可调 */
#define APP_LCD_H_RES     360
#define APP_LCD_V_RES     360

/* ===== 触摸 CST816S（I2C 地址 0x15）===== */
#define APP_TP_I2C_SCL    10
#define APP_TP_I2C_SDA    11
#define APP_TP_RST        1
#define APP_TP_INT        4
#define APP_TP_I2C_ADDR   0x15

/* ===== 共享 I2C 总线（触摸 / IMU / 音频 / 电量 / RTC 共用）===== */
#define APP_I2C_SCL       10
#define APP_I2C_SDA       11
#define APP_IMU_ADDR      0x6B    /* QMI8658 */
#define APP_FUEL_ADDR     0x55    /* BQ27220 */
#define APP_RTC_ADDR      0x51    /* PCF85063 */
#define APP_RTC_INT       6

/* ===== 音频（V2 才启用，第一版不要初始化）===== */
#define APP_AUDIO_MCLK    2
#define APP_AUDIO_BCLK    48
#define APP_AUDIO_LRCK    38
#define APP_AUDIO_DOUT    47    /* 播放 */
#define APP_AUDIO_DIN     39    /* 录音 */
#define APP_AUDIO_PA      9     /* 功放使能 */
#define APP_CODEC_ADDR    0x30  /* ES8311 */
#define APP_ADC_ADDR      0x80  /* ES7210 */


/* ===== 注意 =====
 * PWR 电源键不支持自定义（长按 2s 开关机、短按 1s 开机），
 * 不要用它做聊天按键。
 * BOOT 按键在 GPIO0，是启动配置引脚，第一版也不占用。
 */
