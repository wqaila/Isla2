#include "app_ui.h"

#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "app_limits.h"
#include "app_text.h"
#include "bsp/esp-bsp.h"
#include "esp_log.h"
#include "lvgl.h"

static const char *TAG = "app_ui";

/*
 * 第一版界面：标题 + 正文两段文字，放在可滚动容器里。
 *
 * 线程约束（重要）：
 *   BSP 在独立任务里跑 lv_timer_handler 做渲染，LVGL 本身不是线程安全的。
 *   本文件所有对 LVGL 的写操作都必须持有 bsp_display_lock()，
 *   否则会出现「背光亮了但屏幕没内容」这类静默失败。
 */

static lv_obj_t *s_title;
static lv_obj_t *s_body;
static lv_obj_t *s_container;
static lv_indev_t *s_touch;
static app_state_t s_state = APP_STATE_BOOT;
static bool s_ready;
static char s_answer[APP_MAX_ANSWER_CHARS + 1];

static void style_setup(void)
{
    lv_obj_set_style_bg_color(lv_screen_active(), lv_color_hex(0x101418), 0);
    lv_obj_set_style_text_color(lv_screen_active(), lv_color_hex(0xF3E7EA), 0);
    lv_obj_set_style_pad_all(lv_screen_active(), 16, 0);
}

bool app_ui_init(void)
{
    /*
     * 用微雪官方 BSP 初始化显示：ST77916 + CST816S + 背光。
     * BSP 内部完成 lv_init、显示端口与 LVGL 适配器注册，并启动渲染任务。
     */
    lv_display_t *disp = bsp_display_start();
    if (disp == NULL) {
        s_ready = false;
        ESP_LOGE(TAG, "显示初始化失败，界面功能跳过");
        return false;
    }
    s_touch = bsp_display_get_input_dev();

    /*
     * 关键一：把 BSP 注册的显示设为 LVGL 默认显示。
     * 否则 lv_screen_active() 可能取到别的显示对象，
     * 表现就是「背光亮着，但画面不更新」。
     */
    lv_display_set_default(disp);

    /*
     * 关键二：参数是毫秒超时。传 0 会立即返回、拿不到锁，
     * 后面的 UI 操作就全部白做。这里等 2 秒。
     */
    if (bsp_display_lock(2000) != ESP_OK) {
        s_ready = false;
        ESP_LOGE(TAG, "获取 LVGL 锁失败，界面功能跳过");
        return false;
    }

    style_setup();

    /* 诊断：把关键状态打出来，确认对象真的建在正确显示上 */
    lv_display_t *active = lv_display_get_default();
    ESP_LOGI(TAG, "disp=%p default=%p active_screen=%p res=%dx%d",
             (void *)disp, (void *)active, (void *)lv_screen_active(),
             (int)lv_display_get_horizontal_resolution(active),
             (int)lv_display_get_vertical_resolution(active));

    /*
     * 诊断：画几个已知位置的纯色矩形，不含任何文字。
     * 位置正确 → 传输与映射正常，问题在字形；
     * 位置/形状错乱 → 缓冲步长或面板映射有问题。
     */
    /* 诊断：不创建任何对象，只验证 BSP 渲染任务本身是否稳定 */
    s_title = NULL;
    s_body = NULL;
    s_container = lv_screen_active();

    s_ready = true;

    /*
     * 只做一次失效标记，交给 BSP 的渲染任务去画。
     * 绝不能在持锁时调用 lv_refr_now()：那会与 BSP 渲染任务重复渲染，
     * 实测触发 StoreProhibited 崩溃并导致无限重启。
     */
    lv_obj_invalidate(lv_screen_active());

    bsp_display_unlock();

    bsp_display_backlight_on();
    ESP_LOGI(TAG, "UI 初始化完成，已提交首帧刷新");
    return true;
}

void app_ui_set_state(app_state_t state)
{
    s_state = state;
    if (!s_ready || s_title == NULL) {
        return;
    }
    bsp_display_lock(1000);
    lv_label_set_text(s_title, app_state_title(state));
    lv_obj_invalidate(s_title);
    bsp_display_unlock();
}

void app_ui_show_user(const char *text)
{
    if (text == NULL || !s_ready || s_body == NULL) {
        return;
    }
    char clipped[APP_MAX_MESSAGE_BYTES + 1];
    app_utf8_truncate(text, 120, clipped, sizeof(clipped));

    bsp_display_lock(1000);
    lv_label_set_text_fmt(s_body, "%s\n\n等待回答…", clipped);
    lv_obj_scroll_to_y(s_container, 0, LV_ANIM_OFF);
    bsp_display_unlock();
}

void app_ui_show_answer(const char *text)
{
    if (text == NULL || !s_ready || s_body == NULL) {
        return;
    }
    app_text_plain(text, s_answer, sizeof(s_answer));

    bsp_display_lock(1000);
    lv_label_set_text(s_body, s_answer);
    lv_obj_scroll_to_y(s_container, 0, LV_ANIM_OFF);
    bsp_display_unlock();
}

void app_ui_show_error(app_error_t error)
{
    if (!s_ready || s_body == NULL) {
        return;
    }
    bsp_display_lock(1000);
    lv_label_set_text_fmt(s_body, "%s", app_error_text(error));
    lv_obj_scroll_to_y(s_container, 0, LV_ANIM_OFF);
    bsp_display_unlock();
}

void app_ui_show_settings(const char *wifi_ssid, const char *gateway_url,
                          bool gateway_ready, const char *character_version)
{
    if (!s_ready || s_body == NULL) {
        return;
    }
    bsp_display_lock(1000);
    lv_label_set_text_fmt(s_body,
                          "Wi-Fi：%s\n网关：%s\n状态：%s\n角色：%s",
                          wifi_ssid != NULL && wifi_ssid[0] ? wifi_ssid : "未配置",
                          gateway_url != NULL && gateway_url[0] ? gateway_url : "未配置",
                          gateway_ready ? "就绪" : "不可用",
                          character_version != NULL ? character_version : "未知");
    lv_obj_scroll_to_y(s_container, 0, LV_ANIM_OFF);
    bsp_display_unlock();
}

bool app_ui_scroll(int delta_lines)
{
    if (!s_ready || s_container == NULL) {
        return false;
    }
    bsp_display_lock(1000);
    const int32_t before = lv_obj_get_scroll_y(s_container);
    lv_obj_scroll_by(s_container, 0, delta_lines * 24, LV_ANIM_ON);
    const bool moved = lv_obj_get_scroll_y(s_container) != before;
    bsp_display_unlock();
    return moved;
}

void app_ui_tick(void)
{
    /*
     * BSP 有自己的渲染任务，这里不再调用 lv_timer_handler，
     * 否则会和 BSP 任务并发处理定时器，导致渲染错乱。
     */
}
