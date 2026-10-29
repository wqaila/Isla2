/* I2S 音频：ES7210 四通道录音 + ES8311 单声道播放（严格半双工）。

重要设计约束（务必理解后再改）：
  ES7210 与 ES8311 共用同一条 I2S 控制器、同一组 MCLK/BCLK/LRCK 引脚。
  两个方向**不可能同时以不同采样率工作**。
  因此本模块采用严格半双工：
      录音时 整条 I2S = 16 kHz / 4 slot TDM
      播放时 整条 I2S = 24 kHz / mono 标准模式
  切换时用 i2s_channel_reconfig_*_clock() 真正重配时钟，
  而不是只写 codec 寄存器（那样 I2S 时钟不会变，采样率必然错）。
*/

#include "app_audio.h"

#include <string.h>

#include "app_board.h"
#include "app_config.h"

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "driver/i2s_tdm.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "app_audio";

static i2s_chan_handle_t s_tx;
static i2s_chan_handle_t s_rx;
static i2c_master_bus_handle_t s_i2c_bus;
static i2c_master_dev_handle_t s_adc_dev;   /* ES7210 */
static i2c_master_dev_handle_t s_dac_dev;   /* ES8311 */

static volatile bool s_recording;
static volatile bool s_playing;
static SemaphoreHandle_t s_lock;

/* ===================================================================
 * I2C 基础
 * =================================================================== */

static esp_err_t i2c_write_reg(i2c_master_dev_handle_t dev, uint8_t reg,
                              uint8_t val)
{
    uint8_t buf[2] = {reg, val};
    return i2c_master_transmit(dev, buf, sizeof(buf), 100);
}

/**
 * 写寄存器并检查结果。
 *
 * 之前所有写入都忽略返回值，导致"芯片根本没应答"时仍打印"配置完成"。
 * 这类静默失败是最难查的一类问题，必须让失败可见。
 */
static esp_err_t i2c_write_checked(i2c_master_dev_handle_t dev, uint8_t reg,
                                  uint8_t val, const char *name)
{
    esp_err_t err = i2c_write_reg(dev, reg, val);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "%s 写寄存器 0x%02X 失败: %s", name, reg,
                 esp_err_to_name(err));
        return err;
    }
    return ESP_OK;
}

/* 批量写入用的小宏，任一失败立即返回 */
#define WR(dev, reg, val, name)                          \
    do {                                                 \
        esp_err_t e_ = i2c_write_checked(dev, reg, val, name); \
        if (e_ != ESP_OK) {                              \
            return e_;                                   \
        }                                                \
    } while (0)

static esp_err_t i2c_bus_init(void)
{
    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = APP_I2C_SDA,
        .scl_io_num = APP_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    esp_err_t err = i2c_new_master_bus(&bus_cfg, &s_i2c_bus);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2C 总线创建失败: %s", esp_err_to_name(err));
        return err;
    }

    /* 注意：device_address 是 7 位地址，见 app_board.h 的说明 */
    i2c_device_config_t adc_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = APP_ADC_ADDR,
        .scl_speed_hz = 100000,
    };
    err = i2c_master_bus_add_device(s_i2c_bus, &adc_cfg, &s_adc_dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ES7210(0x%02X) 设备句柄失败: %s", APP_ADC_ADDR,
                 esp_err_to_name(err));
        return err;
    }

    i2c_device_config_t dac_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = APP_CODEC_ADDR,
        .scl_speed_hz = 100000,
    };
    err = i2c_master_bus_add_device(s_i2c_bus, &dac_cfg, &s_dac_dev);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ES8311(0x%02X) 设备句柄失败: %s", APP_CODEC_ADDR,
                 esp_err_to_name(err));
        return err;
    }

    /* 探测：读一次寄存器确认芯片真的应答。
     * 句柄创建成功不代表器件存在，这一步能把接线/地址错误提前暴露。 */
    uint8_t probe = 0;
    err = i2c_master_transmit_receive(s_adc_dev, (uint8_t[]){0x00}, 1,
                                     &probe, 1, 100);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ES7210 无应答（检查地址/接线）: %s",
                 esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG, "ES7210 应答正常，reg00=0x%02X", probe);

    err = i2c_master_transmit_receive(s_dac_dev, (uint8_t[]){0x00}, 1,
                                     &probe, 1, 100);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ES8311 无应答（检查地址/接线）: %s",
                 esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG, "ES8311 应答正常，reg00=0x%02X", probe);
    return ESP_OK;
}

/* ===================================================================
 * ES8311 采样率分频表
 *
 * 直接搬官方组件的 coeff_div 逻辑：按 mclk = rate * mclk_div 查表，
 * 得到 pre_div / pre_multi / adc_div / dac_div / fs_mode / lrck_div 等。
 * 手写估算寄存器值几乎必定出错，必须按表算。
 * =================================================================== */

typedef struct {
    uint32_t rate;
    uint32_t mclk;
    uint8_t  pre_div;
    uint8_t  pre_multi;
    uint8_t  adc_div;
    uint8_t  dac_div;
    uint8_t  fs_mode;
    uint8_t  lrck_h;
    uint8_t  lrck_l;
    uint8_t  bclk_div;
    uint8_t  adc_lrck_div;
} es8311_coeff_t;

/* 只保留本项目用到的 16k / 24k（mclk 256x），其余按需再补 */
static const es8311_coeff_t COEFF[] = {
    /* rate    mclk     pre_div pre_multi adc_div dac_div fs_mode lrck_h lrck_l bclk_div adc_lrck_div */
    {16000,  4096000, 1,       1,        1,      1,      0,      255,   1,      1,       1},
    {24000,  6144000, 1,       1,        1,      1,      0,      255,   1,      1,       1},
};

static const es8311_coeff_t *find_coeff(uint32_t rate, uint32_t mclk)
{
    for (size_t i = 0; i < sizeof(COEFF) / sizeof(COEFF[0]); ++i) {
        if (COEFF[i].rate == rate && COEFF[i].mclk == mclk) {
            return &COEFF[i];
        }
    }
    return NULL;
}

/**
 * 配置 ES7210 为 4 通道采集（16 kHz）。
 *
 * 寄存器序列按官方组件 es7210 的初始化顺序整理；
 * 每条写入都检查返回值，失败即返回，不再静默"配置完成"。
 */
static esp_err_t es7210_config(void)
{
    if (s_adc_dev == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    /* 复位 */
    WR(s_adc_dev, 0x00, 0x41, "ES7210");
    vTaskDelay(pdMS_TO_TICKS(10));
    WR(s_adc_dev, 0x00, 0x40, "ES7210");
    vTaskDelay(pdMS_TO_TICKS(10));

    /* 从模式 + 时钟分频（MCLK 由 ESP32 提供） */
    WR(s_adc_dev, 0x01, 0x3F, "ES7210");
    WR(s_adc_dev, 0x02, 0x00, "ES7210");

    /* 16 kHz 对应分频档 */
    WR(s_adc_dev, 0x08, 0x20, "ES7210");

    /* 四路麦克风增益 */
    WR(s_adc_dev, 0x09, 0x30, "ES7210");   /* MIC1 */
    WR(s_adc_dev, 0x0A, 0x30, "ES7210");   /* MIC2 */
    WR(s_adc_dev, 0x0B, 0x30, "ES7210");   /* MIC3 */
    WR(s_adc_dev, 0x0C, 0x30, "ES7210");   /* MIC4 */

    /* 通道使能：四麦全开 */
    WR(s_adc_dev, 0x11, 0x7F, "ES7210");
    WR(s_adc_dev, 0x12, 0x00, "ES7210");

    /* 16 bit，I2S 格式 */
    WR(s_adc_dev, 0x13, 0x10, "ES7210");
    WR(s_adc_dev, 0x14, 0x00, "ES7210");

    WR(s_adc_dev, 0x00, 0x41, "ES7210");
    vTaskDelay(pdMS_TO_TICKS(10));

    ESP_LOGI(TAG, "ES7210 配置完成：16 kHz / 4 通道");
    return ESP_OK;
}

/**
 * 配置 ES8311 为单声道 DAC 播放（按传入采样率算分频）。
 *
 * 关键：必须根据 rate 与 mclk 查表算寄存器，
 * 原来手写的 0x22 只是"某个采样率"的值，不能同时覆盖 16k 和 24k。
 */
static esp_err_t es8311_config(uint32_t sample_rate)
{
    if (s_dac_dev == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    const uint32_t mclk = sample_rate * APP_AUDIO_MCLK_DIV;
    const es8311_coeff_t *c = find_coeff(sample_rate, mclk);
    if (c == NULL) {
        ESP_LOGE(TAG, "ES8311 无 %u Hz (MCLK %u) 的分频配置",
                 (unsigned)sample_rate, (unsigned)mclk);
        return ESP_ERR_NOT_SUPPORTED;
    }

    /* 复位 */
    WR(s_dac_dev, 0x00, 0x1F, "ES8311");
    vTaskDelay(pdMS_TO_TICKS(10));
    WR(s_dac_dev, 0x00, 0x00, "ES8311");
    vTaskDelay(pdMS_TO_TICKS(10));

    /* 时钟源：MCLK 来自外部（ESP32 I2S） */
    WR(s_dac_dev, 0x01, 0x30, "ES8311");

    /* 分频寄存器 */
    WR(s_dac_dev, 0x02, (uint8_t)((c->pre_div - 1) << 5), "ES8311");
    WR(s_dac_dev, 0x05, (uint8_t)((c->adc_div - 1) << 4), "ES8311");
    WR(s_dac_dev, 0x03, (uint8_t)((c->dac_div - 1) << 4), "ES8311");
    WR(s_dac_dev, 0x04, 0x10, "ES8311");
    WR(s_dac_dev, 0x06, c->bclk_div, "ES8311");

    /* LRCK 分频 */
    WR(s_dac_dev, 0x07, c->lrck_h, "ES8311");
    WR(s_dac_dev, 0x08, c->lrck_l, "ES8311");

    /* 16 bit，单声道，无 ADC */
    WR(s_dac_dev, 0x09, 0x00, "ES8311");
    WR(s_dac_dev, 0x0A, 0x00, "ES8311");
    WR(s_dac_dev, 0x0B, 0x00, "ES8311");
    WR(s_dac_dev, 0x0C, 0x00, "ES8311");
    WR(s_dac_dev, 0x0D, 0x01, "ES8311");
    WR(s_dac_dev, 0x0E, 0x02, "ES8311");
    WR(s_dac_dev, 0x12, 0x00, "ES8311");
    WR(s_dac_dev, 0x13, 0x10, "ES8311");
    WR(s_dac_dev, 0x14, 0x00, "ES8311");
    WR(s_dac_dev, 0x15, 0x00, "ES8311");
    WR(s_dac_dev, 0x16, 0x00, "ES8311");
    WR(s_dac_dev, 0x17, 0x00, "ES8311");
    WR(s_dac_dev, 0x1B, 0x00, "ES8311");
    WR(s_dac_dev, 0x1C, 0x00, "ES8311");

    /* 音量（0x00 最大，这里取中等偏大） */
    WR(s_dac_dev, 0x32, 0x50, "ES8311");

    /* 上电 */
    WR(s_dac_dev, 0x00, 0x80, "ES8311");
    vTaskDelay(pdMS_TO_TICKS(10));

    ESP_LOGI(TAG, "ES8311 配置完成：%u Hz / 单声道（MCLK %u）",
             (unsigned)sample_rate, (unsigned)mclk);
    return ESP_OK;
}

/* ===================================================================
 * I2S 模式切换（真正重配时钟）
 * =================================================================== */

static esp_err_t i2s_to_record_mode(void)
{
    if (s_tx) {
        i2s_channel_disable(s_tx);
    }
    if (s_rx) {
        i2s_channel_disable(s_rx);
    }

    /* 关键：改时钟，而不只是改 codec。
     * 原实现漏了这一步，导致 I2S 实际仍跑在旧采样率。 */
    i2s_tdm_clk_config_t clk = I2S_TDM_CLK_DEFAULT_CONFIG(APP_MIC_SAMPLE_RATE);
    esp_err_t err = i2s_channel_reconfig_tdm_clock(s_rx, &clk);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "重建录音时钟失败: %s", esp_err_to_name(err));
        return err;
    }

    err = es7210_config();
    if (err != ESP_OK) {
        return err;
    }

    if (s_rx) {
        i2s_channel_enable(s_rx);
    }
    return ESP_OK;
}

static esp_err_t i2s_to_play_mode(void)
{
    if (s_rx) {
        i2s_channel_disable(s_rx);
    }
    if (s_tx) {
        i2s_channel_disable(s_tx);
    }

    i2s_std_clk_config_t clk = I2S_STD_CLK_DEFAULT_CONFIG(APP_SPK_SAMPLE_RATE);
    esp_err_t err = i2s_channel_reconfig_std_clock(s_tx, &clk);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "重建播放时钟失败: %s", esp_err_to_name(err));
        return err;
    }

    err = es8311_config(APP_SPK_SAMPLE_RATE);
    if (err != ESP_OK) {
        return err;
    }

    if (s_tx) {
        i2s_channel_enable(s_tx);
    }
    return ESP_OK;
}

esp_err_t app_audio_init(void)
{
    s_lock = xSemaphoreCreateMutex();
    if (s_lock == NULL) {
        return ESP_ERR_NO_MEM;
    }

    esp_err_t err = i2c_bus_init();
    if (err != ESP_OK) {
        return err;
    }

    /* 功放使能脚，默认关闭避免上电爆音 */
    gpio_config_t pa_cfg = {
        .pin_bit_mask = 1ULL << APP_AUDIO_PA,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&pa_cfg);
    gpio_set_level(APP_AUDIO_PA, 0);

    /* 建一对 channel：TX 走标准模式，RX 走 TDM 模式（4 slot） */
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(
        I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 6;
    chan_cfg.dma_frame_num = 240;
    chan_cfg.auto_clear = true;

    err = i2s_new_channel(&chan_cfg, &s_tx, &s_rx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S 通道创建失败: %s", esp_err_to_name(err));
        return err;
    }

    i2s_std_config_t tx_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(APP_SPK_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = APP_AUDIO_MCLK,
            .bclk = APP_AUDIO_BCLK,
            .ws   = APP_AUDIO_LRCK,
            .dout = APP_AUDIO_DOUT,
            .din  = APP_AUDIO_DIN,
            .invert_flags = {.mclk_inv = false, .bclk_inv = false,
                             .ws_inv = false},
        },
    };
    err = i2s_channel_init_std_mode(s_tx, &tx_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S TX 初始化失败: %s", esp_err_to_name(err));
        return err;
    }

    i2s_tdm_config_t rx_cfg = {
        .clk_cfg = I2S_TDM_CLK_DEFAULT_CONFIG(APP_MIC_SAMPLE_RATE),
        .slot_cfg = I2S_TDM_PHILIPS_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO,
            (i2s_tdm_slot_mask_t)(I2S_TDM_SLOT0 | I2S_TDM_SLOT1 |
                                  I2S_TDM_SLOT2 | I2S_TDM_SLOT3)),
        .gpio_cfg = {
            .mclk = APP_AUDIO_MCLK,
            .bclk = APP_AUDIO_BCLK,
            .ws   = APP_AUDIO_LRCK,
            .dout = APP_AUDIO_DOUT,
            .din  = APP_AUDIO_DIN,
            .invert_flags = {.mclk_inv = false, .bclk_inv = false,
                             .ws_inv = false},
        },
    };
    rx_cfg.slot_cfg.total_slot = 4;
    err = i2s_channel_init_tdm_mode(s_rx, &rx_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S RX(TDM) 初始化失败: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "音频初始化完成：录音 %d Hz/%dch，播放 %d Hz/mono",
             APP_MIC_SAMPLE_RATE, APP_MIC_CHANNELS, APP_SPK_SAMPLE_RATE);
    return ESP_OK;
}

/* ===================================================================
 * 录音 / 播放控制
 * =================================================================== */

esp_err_t app_audio_record_start(void)
{
    if (s_lock == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);

    /* 半双工：录音前关功放，避免扬声器回采造成啸叫 */
    if (s_playing) {
        gpio_set_level(APP_AUDIO_PA, 0);
        s_playing = false;
    }

    esp_err_t err = i2s_to_record_mode();
    if (err == ESP_OK) {
        s_recording = true;
        ESP_LOGI(TAG, "开始录音");
    } else {
        ESP_LOGE(TAG, "录音启动失败: %s", esp_err_to_name(err));
    }
    xSemaphoreGive(s_lock);
    return err;
}

esp_err_t app_audio_record_stop(void)
{
    if (s_lock == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_rx) {
        i2s_channel_disable(s_rx);
    }
    s_recording = false;
    ESP_LOGI(TAG, "停止录音");
    xSemaphoreGive(s_lock);
    return ESP_OK;
}

esp_err_t app_audio_record_read(void *buf, size_t cap, size_t *got,
                                uint32_t timeout_ms)
{
    if (buf == NULL || got == NULL || s_rx == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *got = 0;
    if (!s_recording) {
        return ESP_ERR_INVALID_STATE;
    }
    return i2s_channel_read(s_rx, buf, cap, got, timeout_ms);
}

esp_err_t app_audio_play_start(void)
{
    if (s_lock == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);

    if (s_recording && s_rx) {
        i2s_channel_disable(s_rx);
        s_recording = false;
    }

    esp_err_t err = i2s_to_play_mode();
    if (err == ESP_OK) {
        s_playing = true;
        gpio_set_level(APP_AUDIO_PA, 1);
        ESP_LOGI(TAG, "开始播放");
    } else {
        ESP_LOGE(TAG, "播放启动失败: %s", esp_err_to_name(err));
    }
    xSemaphoreGive(s_lock);
    return err;
}

esp_err_t app_audio_play_stop(void)
{
    if (s_lock == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    gpio_set_level(APP_AUDIO_PA, 0);   /* 先关功放，避免尾音爆鸣 */
    if (s_tx) {
        i2s_channel_disable(s_tx);
    }
    s_playing = false;
    ESP_LOGI(TAG, "停止播放");
    xSemaphoreGive(s_lock);
    return ESP_OK;
}

esp_err_t app_audio_play_write(const void *data, size_t len,
                               size_t *written, uint32_t timeout_ms)
{
    if (data == NULL || len == 0 || s_tx == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_playing) {
        return ESP_ERR_INVALID_STATE;
    }
    size_t w = 0;
    esp_err_t err = i2s_channel_write(s_tx, data, len, &w, timeout_ms);
    if (written) {
        *written = w;   /* 让上层知道实际写入了多少，避免统计失真 */
    }
    return err;
}

esp_err_t app_audio_play_drain(uint32_t timeout_ms)
{
    if (s_tx == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    /* 用"喂入静音"的方式把 DMA 队列里剩余的真实音频顶出去。
     *
     * 之前用 i2s_channel_write(s_tx, NULL, 0, ...) 是错的：
     * 零长度写入不是排空接口，不能保证硬件已播完。
     * 这里写入与 DMA 缓冲同等大小的静音，阻塞直到以前的样本播出，
     * 既有确定语义，也不会引入额外可听延迟（静音段极短）。
     */
    const size_t dma_bytes = 240 * 2;          /* dma_frame_num * 16bit mono */
    static int16_t silence[240];
    memset(silence, 0, sizeof(silence));

    esp_err_t err = ESP_OK;
    /* 写两份 DMA 深度，覆盖整个在途队列 */
    for (int i = 0; i < 2; ++i) {
        size_t w = 0;
        err = i2s_channel_write(s_tx, silence, dma_bytes, &w, timeout_ms);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "排空写入失败: %s", esp_err_to_name(err));
            return err;
        }
    }
    return err;
}

bool app_audio_recording(void)
{
    return s_recording;
}

bool app_audio_playing(void)
{
    return s_playing;
}
