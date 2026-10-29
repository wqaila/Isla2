/*
 * 屏幕显示实现。
 *
 * 说明：本项目**复用板子里已有的显示能力**（用户已烧录好 ST77916 驱动与
 * 动画资源）。这里只提供一个最简文字的显示层，用于联调时观察状态。
 *
 * 如果不启用图形库，退化为"串口 + 背光提示"，不影响语音链路。
 * 通过 CONFIG_APP_UI_LCD_ENABLE 控制是否编译 LCD 部分。
 */

#include "app_ui.h"

#include <stdio.h>
#include <string.h>

#include "app_board.h"
#include "app_config.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "app_ui";

#if defined(CONFIG_APP_UI_LCD_ENABLE) && CONFIG_APP_UI_LCD_ENABLE

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_st77916.h"

static esp_lcd_panel_handle_t s_panel;
static uint16_t *s_fb;                    /* 帧缓冲，PSRAM */
static SemaphoreHandle_t s_ui_lock;

#define UI_LINE_MAX 48

static char s_status[UI_LINE_MAX];
static char s_asr[UI_LINE_MAX];
static char s_reply[256];
static char s_hint[UI_LINE_MAX];

/* 最简 5x7 点阵字模（仅 ASCII，中文显示需要字库，
 * 本层不内置中文字库 —— 中文显示留给已有的动画固件那套 UI）。 */
static const uint8_t FONT5X7[][5] = {
    /* 只放必要的字符，完整字库太大。实际中文字幕显示由 BSP 的 LVGL 负责。 */
};

static void fill_rect(int x0, int y0, int w, int h, uint16_t color)
{
    if (s_fb == NULL) {
        return;
    }
    for (int y = y0; y < y0 + h && y < APP_LCD_V_RES; ++y) {
        if (y < 0) {
            continue;
        }
        uint16_t *row = s_fb + (size_t)y * APP_LCD_H_RES;
        for (int x = x0; x < x0 + w && x < APP_LCD_H_RES; ++x) {
            if (x >= 0) {
                row[x] = color;
            }
        }
    }
}

static void draw_line_text(int x, int y, const char *text, uint16_t color)
{
    /* 极简实现：每个字符占 8x8 像素块，用方块表示"有字"。
     * 真正的字体渲染交给后续接入的字库；这里只保证
     * 联调时能看到"有没有数据在流动"。 */
    if (text == NULL) {
        return;
    }
    int cx = x;
    for (const char *p = text; *p && cx + 8 <= APP_LCD_H_RES; ++p) {
        if (*p != ' ' && *p != '\n') {
            fill_rect(cx + 2, y + 2, 4, 4, color);
        }
        cx += 8;
    }
}

esp_err_t app_ui_init(void)
{
    s_ui_lock = xSemaphoreCreateMutex();
    if (s_ui_lock == NULL) {
        return ESP_ERR_NO_MEM;
    }

    /* 背光脚：先关再开，避免上电闪白 */
    gpio_config_t bl = {
        .pin_bit_mask = 1ULL << APP_LCD_BL,
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&bl);
    gpio_set_level(APP_LCD_BL, 0);

    /* QSPI 总线 */
    spi_bus_config_t bus_cfg = {
        .sclk_io_num = APP_LCD_PCLK,
        .data0_io_num = APP_LCD_DATA0,
        .data1_io_num = APP_LCD_DATA1,
        .data2_io_num = APP_LCD_DATA2,
        .data3_io_num = APP_LCD_DATA3,
        .max_transfer_sz = APP_LCD_H_RES * 80 * sizeof(uint16_t),
    };
    esp_err_t err = spi_bus_initialize(SPI2_HOST, &bus_cfg, SPI_DMA_CH_AUTO);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "QSPI 总线初始化失败: %s", esp_err_to_name(err));
        return err;
    }

    esp_lcd_panel_io_spi_config_t io_cfg = {
        .cs_gpio_num = APP_LCD_CS,
        .dc_gpio_num = -1,
        .spi_mode = 0,
        .pclk_hz = 40 * 1000 * 1000,
        .trans_queue_depth = 10,
        .lcd_cmd_bits = 32,
        .lcd_param_bits = 8,
        .flags.quad_mode = true,
    };
    esp_lcd_panel_io_handle_t io = NULL;
    err = esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST,
                                   &io_cfg, &io);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "面板 IO 创建失败: %s", esp_err_to_name(err));
        return err;
    }

    esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = APP_LCD_RST,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
    };
    err = esp_lcd_new_panel_st77916(io, &panel_cfg, &s_panel);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ST77916 面板创建失败: %s", esp_err_to_name(err));
        return err;
    }

    esp_lcd_panel_reset(s_panel);
    esp_lcd_panel_init(s_panel);
    esp_lcd_panel_mirror(s_panel, true, false);
    esp_lcd_panel_disp_on_off(s_panel, true);

    /* 帧缓冲放 PSRAM */
    s_fb = heap_caps_malloc(APP_LCD_H_RES * APP_LCD_V_RES * sizeof(uint16_t),
                            MALLOC_CAP_SPIRAM);
    if (s_fb == NULL) {
        ESP_LOGE(TAG, "帧缓冲分配失败（PSRAM 不足）");
        return ESP_ERR_NO_MEM;
    }
    memset(s_fb, 0, APP_LCD_H_RES * APP_LCD_V_RES * sizeof(uint16_t));

    gpio_set_level(APP_LCD_BL, 1);
    ESP_LOGI(TAG, "LCD 初始化完成 %dx%d", APP_LCD_H_RES, APP_LCD_V_RES);
    return ESP_OK;
}

void app_ui_flush(void)
{
    if (s_panel == NULL || s_fb == NULL) {
        return;
    }
    if (s_ui_lock) {
        xSemaphoreTake(s_ui_lock, portMAX_DELAY);
    }
    esp_lcd_panel_draw_bitmap(s_panel, 0, 0, APP_LCD_H_RES, APP_LCD_V_RES, s_fb);
    if (s_ui_lock) {
        xSemaphoreGive(s_ui_lock);
    }
}

void app_ui_set_status(const char *text)
{
    if (text == NULL) {
        return;
    }
    if (s_ui_lock) {
        xSemaphoreTake(s_ui_lock, portMAX_DELAY);
    }
    snprintf(s_status, sizeof(s_status), "%s", text);
    if (s_ui_lock) {
        xSemaphoreGive(s_ui_lock);
    }
    fill_rect(0, 0, APP_LCD_H_RES, 40, 0x0000);
    draw_line_text(8, 12, s_status, 0x07E0);
    app_ui_flush();
}

void app_ui_set_asr(const char *text)
{
    if (text == NULL) {
        return;
    }
    if (s_ui_lock) {
        xSemaphoreTake(s_ui_lock, portMAX_DELAY);
    }
    snprintf(s_asr, sizeof(s_asr), "%s", text);
    if (s_ui_lock) {
        xSemaphoreGive(s_ui_lock);
    }
    fill_rect(0, 60, APP_LCD_H_RES, 50, 0x0000);
    draw_line_text(8, 72, s_asr, 0xFFFF);
    app_ui_flush();
}

void app_ui_append_reply(const char *delta)
{
    if (delta == NULL) {
        return;
    }
    if (s_ui_lock) {
        xSemaphoreTake(s_ui_lock, portMAX_DELAY);
    }
    size_t cur = strlen(s_reply);
    size_t room = sizeof(s_reply) - cur - 1;
    if (room > 0) {
        strncat(s_reply, delta, room);
    }
    if (s_ui_lock) {
        xSemaphoreGive(s_ui_lock);
    }
    fill_rect(0, 130, APP_LCD_H_RES, 120, 0x0000);
    draw_line_text(8, 142, s_reply, 0xF800);
    app_ui_flush();
}

void app_ui_clear_reply(void)
{
    if (s_ui_lock) {
        xSemaphoreTake(s_ui_lock, portMAX_DELAY);
    }
    s_reply[0] = '\0';
    if (s_ui_lock) {
        xSemaphoreGive(s_ui_lock);
    }
    fill_rect(0, 130, APP_LCD_H_RES, 120, 0x0000);
    app_ui_flush();
}

void app_ui_set_hint(const char *text)
{
    if (text == NULL) {
        return;
    }
    if (s_ui_lock) {
        xSemaphoreTake(s_ui_lock, portMAX_DELAY);
    }
    snprintf(s_hint, sizeof(s_hint), "%s", text);
    if (s_ui_lock) {
        xSemaphoreGive(s_ui_lock);
    }
    fill_rect(0, 300, APP_LCD_H_RES, 40, 0x0000);
    draw_line_text(8, 312, s_hint, 0x07FF);
    app_ui_flush();
}

void app_ui_show_error(const char *code, const char *message)
{
    ESP_LOGE(TAG, "UI 错误: %s - %s", code ? code : "", message ? message : "");
    fill_rect(0, 300, APP_LCD_H_RES, 60, 0x0000);
    char buf[96];
    snprintf(buf, sizeof(buf), "ERR %s", code ? code : "");
    draw_line_text(8, 308, buf, 0xF800);
    draw_line_text(8, 326, message ? message : "", 0xF800);
    app_ui_flush();
}

#else  /* 未启用 LCD：全部退化为串口日志 */

esp_err_t app_ui_init(void)
{
    ESP_LOGW(TAG, "LCD 未启用（CONFIG_APP_UI_LCD_ENABLE 关闭），仅记录日志");
    return ESP_OK;
}

void app_ui_flush(void) {}
void app_ui_set_status(const char *text)
{
    ESP_LOGI(TAG, "[状态] %s", text ? text : "");
}
void app_ui_set_asr(const char *text)
{
    ESP_LOGI(TAG, "[ASR] %s", text ? text : "");
}
void app_ui_append_reply(const char *delta)
{
    ESP_LOGI(TAG, "[回复+] %s", delta ? delta : "");
}
void app_ui_clear_reply(void)
{
    ESP_LOGI(TAG, "[回复] 清空");
}
void app_ui_set_hint(const char *text)
{
    ESP_LOGI(TAG, "[提示] %s", text ? text : "");
}
void app_ui_show_error(const char *code, const char *message)
{
    ESP_LOGE(TAG, "[错误] %s: %s", code ? code : "", message ? message : "");
}

#endif /* CONFIG_APP_UI_LCD_ENABLE */
