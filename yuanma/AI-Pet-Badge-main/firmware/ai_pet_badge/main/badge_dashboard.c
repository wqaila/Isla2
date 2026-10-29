#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
#include <sys/time.h>
#include "badge_dashboard.h"
#include "badge_character.h"
#include "bsp/esp-bsp.h"
#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "pcf85063a.h"
#include "src/draw/snapshot/lv_snapshot.h"

LV_FONT_DECLARE(badge_ui_14);
LV_FONT_DECLARE(badge_name_20);
LV_FONT_DECLARE(badge_clock_30);
LV_FONT_DECLARE(badge_icons_26);

static const char *TAG = "dashboard";
static lv_obj_t *clock_label, *date_label, *day_label, *name_label, *temp_label;
static lv_obj_t *weather_label, *weather_icon, *motion_label, *hint_label, *connection_dot;
/* 语音状态行：显示语音客户端连接与对话阶段 */
static lv_obj_t *voice_label;
static pcf85063a_dev_t rtc;
static bool rtc_ready;
static volatile bool moving, pending, dizzy, recovering;
static _Atomic uint32_t revision = 1;
static uint32_t previous_dot = 0xC8B8D2, previous_icon = 0xB1A7CC;
static portMUX_TYPE state_mux = portMUX_INITIALIZER_UNLOCKED;
static struct {
    bool connected, weather_valid, day;
    float temperature;
    int code;
    time_t observed;
    /* 语音状态：0 未启用 / 1 连接中 / 2 待命 / 3 录音 / 4 播放 */
    int voice_state;
} state;

static lv_obj_t *label(lv_obj_t *parent, const lv_font_t *font, uint32_t color, const char *text)
{
    lv_obj_t *obj = lv_label_create(parent);
    lv_obj_set_style_text_font(obj, font, 0);
    lv_obj_set_style_text_color(obj, lv_color_hex(color), 0);
    lv_label_set_text(obj, text);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_CLICKABLE);
    return obj;
}

static void text_if_changed(lv_obj_t *obj, const char *text)
{
    if (strcmp(lv_label_get_text(obj), text)) { lv_label_set_text(obj, text); ++revision; }
}

static lv_obj_t *card(lv_obj_t *parent, int x, int y, int width, int height)
{
    lv_obj_t *obj = lv_obj_create(parent);
    lv_obj_remove_style_all(obj);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, width, height);
    lv_obj_set_style_bg_opa(obj, LV_OPA_TRANSP, 0);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    return obj;
}

static const char *weather_text(int code)
{
    if (code == 0) return "晴";
    if (code == 1 || code == 2) return "多云";
    if (code == 3) return "阴";
    if (code == 45 || code == 48) return "雾";
    if (code >= 51 && code <= 57) return "毛毛雨";
    if (code == 61 || code == 80) return "小雨";
    if (code == 63 || code == 81) return "中雨";
    if (code == 65 || code == 82) return "大雨";
    if (code == 66 || code == 67) return "冻雨";
    if (code >= 71 && code <= 77) return "雪";
    if (code == 85 || code == 86) return "阵雪";
    if (code >= 95 && code <= 99) return "雷雨";
    return "天气";
}

bool badge_dashboard_time_valid(void) { return time(NULL) >= 1704067200LL; }

static void refresh(lv_timer_t *timer)
{
    (void)timer;
    char text[48];
    time_t now = time(NULL);
    struct tm local;
    localtime_r(&now, &local);
    if (badge_dashboard_time_valid()) {
        strftime(text, sizeof(text), "%H:%M", &local);
        text_if_changed(clock_label, text);
        snprintf(text, sizeof(text), "%02d月%02d日", local.tm_mon + 1, local.tm_mday);
        text_if_changed(date_label, text);
        const char *days[] = {"周日", "周一", "周二", "周三", "周四", "周五", "周六"};
        text_if_changed(day_label, days[local.tm_wday]);
    }
    portENTER_CRITICAL(&state_mux);
    bool connected = state.connected, valid = state.weather_valid, day = state.day;
    float temperature = state.temperature;
    int code = state.code;
    time_t observed = state.observed;
    portEXIT_CRITICAL(&state_mux);
    uint32_t dot = connected ? 0x79B5A7 : 0xC8B8D2;
    if (dot != previous_dot) {
        lv_obj_set_style_bg_color(connection_dot, lv_color_hex(dot), 0);
        previous_dot = dot; ++revision;
    }
    text_if_changed(motion_label, recovering ? "恢复中" : dizzy ? "头晕中" : moving ? "动作中" : "陪伴中");
    text_if_changed(hint_label, dizzy ? "稍后互动" : pending ? "已记下" : "轻触互动");

    /* 语音状态：没启用时不显示任何字样，保持原界面干净 */
    {
        int vs = state.voice_state;
        const char *vt = NULL;
        uint32_t vc = 0xB8A1B8;
        switch (vs) {
        case 1: vt = "语音连接中"; vc = 0xB8A1B8; break;
        case 2: vt = "语音待命";   vc = 0x79B5A7; break;
        case 3: vt = "在听你说";   vc = 0xE5B461; break;
        case 4: vt = "正在回应";   vc = 0xC499CB; break;
        default: vt = ""; break;    /* 0 = 未启用 */
        }
        if (vt && voice_label) {
            text_if_changed(voice_label, vt);
        }
    }
    if (valid) {
        snprintf(text, sizeof(text), "%.0f°C", temperature);
        text_if_changed(temp_label, text);
        text_if_changed(weather_label, now - observed > 3600 ? "待更新" : weather_text(code));
        const char *icon = "\xEF\x83\x82"; // cloud U+F0C2
        uint32_t color = 0x9A9BCA;
        if (code == 0) { icon = day ? "\xEF\x86\x85" : "\xEF\x86\x86"; color = day ? 0xE5B461 : 0x9A88C2; }
        else if (code >= 95) { icon = "\xEF\x83\xA7"; color = 0xC499CB; }
        else if ((code >= 71 && code <= 77) || code == 85 || code == 86) icon = "\xEF\x8B\x9C";
        else if (code >= 51) { icon = "\xEF\x83\xA9"; color = 0x8DADD0; }
        text_if_changed(weather_icon, icon);
        if (color != previous_icon) {
            lv_obj_set_style_text_color(weather_icon, lv_color_hex(color), 0);
            previous_icon = color; ++revision;
        }
    } else {
        text_if_changed(weather_label, connected ? (badge_dashboard_time_valid() ? "更新中" : "校时中") : "联网中");
    }
}

void badge_dashboard_create(lv_obj_t *screen)
{
    clock_label = label(screen, &badge_clock_30, 0x765C8B, "--:--");
    lv_obj_set_pos(clock_label, 99, 24);
    date_label = label(screen, &badge_ui_14, 0x9B83A3, "校时中");
    lv_obj_set_pos(date_label, 203, 28);
    day_label = label(screen, &badge_ui_14, 0xAE9ABA, "");
    lv_obj_set_pos(day_label, 203, 47);
    name_label = label(screen, &badge_name_20, 0x8A648D, BADGE_CHARACTER_NAME);
    lv_obj_set_width(name_label, 132);
    lv_obj_set_style_text_align(name_label, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(name_label, LV_ALIGN_TOP_MID, 0, 65);
    lv_obj_t *weather = card(screen, 28, 151, 82, 126);
    lv_obj_t *city = label(weather, &badge_ui_14, 0x9C87A8, "深圳");
    lv_obj_align(city, LV_ALIGN_TOP_MID, 0, 8);
    weather_icon = label(weather, &badge_icons_26, 0xB1A7CC, "\xEF\x83\x82");
    lv_obj_align(weather_icon, LV_ALIGN_TOP_MID, 0, 33);
    temp_label = label(weather, &lv_font_montserrat_20, 0x827096, "--°");
    lv_obj_align(temp_label, LV_ALIGN_TOP_MID, 0, 66);
    weather_label = label(weather, &badge_ui_14, 0xA391AE, "待同步");
    lv_obj_align(weather_label, LV_ALIGN_TOP_MID, 0, 89);
    lv_obj_t *credit = label(weather, &lv_font_montserrat_12, 0xB7ABC3, "Open-Meteo");
    lv_obj_align(credit, LV_ALIGN_BOTTOM_MID, 0, -5);
    lv_obj_t *mood = card(screen, 262, 174, 77, 76);
    motion_label = label(mood, &badge_ui_14, 0xAB7E9F, "陪伴中");
    lv_obj_align(motion_label, LV_ALIGN_TOP_MID, 0, 15);
    hint_label = label(mood, &badge_ui_14, 0xB8A1B8, "轻触互动");
    lv_obj_align(hint_label, LV_ALIGN_TOP_MID, 0, 42);
    connection_dot = lv_obj_create(screen);
    lv_obj_remove_style_all(connection_dot);
    lv_obj_set_size(connection_dot, 6, 6);
    lv_obj_set_style_radius(connection_dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(connection_dot, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(connection_dot, lv_color_hex(0xC8B8D2), 0);
    lv_obj_set_pos(connection_dot, 297, 164);
    lv_obj_remove_flag(connection_dot, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_t *footer = label(screen, &badge_ui_14, 0xB299B9, "在你身边");
    lv_obj_align(footer, LV_ALIGN_BOTTOM_MID, 0, -9);
    /* 语音状态放在页脚上方，未启用时为空串，不占视觉空间 */
    voice_label = label(screen, &badge_ui_14, 0xB8A1B8, "");
    lv_obj_align(voice_label, LV_ALIGN_BOTTOM_MID, 0, -28);
    lv_timer_create(refresh, 100, NULL);
}

void badge_dashboard_start(void)
{
    setenv("TZ", "CST-8", 1);
    tzset();
    rtc_ready = pcf85063a_init(&rtc, bsp_i2c_get_handle(), PCF85063A_ADDRESS) == ESP_OK;
    if (!rtc_ready) return;
    pcf85063a_datetime_t value;
    uint8_t seconds;
    if (pcf85063a_read_register(&rtc, PCF85063A_RTC_SECOND_ADDR, &seconds, 1) != ESP_OK || (seconds & 0x80) ||
        pcf85063a_get_time_date(&rtc, &value) != ESP_OK || value.year < 2024 || value.year > 2099 ||
        value.month < 1 || value.month > 12 || value.day < 1 || value.day > 31 ||
        value.hour > 23 || value.min > 59 || value.sec > 59) return;
    struct tm local = {.tm_year = value.year - 1900, .tm_mon = value.month - 1, .tm_mday = value.day,
                       .tm_hour = value.hour, .tm_min = value.min, .tm_sec = value.sec, .tm_isdst = -1};
    struct timeval now = {.tv_sec = mktime(&local)};
    settimeofday(&now, NULL);
    ESP_LOGI(TAG, "Clock restored from RTC");
}

void badge_dashboard_set_time(time_t epoch)
{
    if (epoch < 1704067200LL || epoch > 4102444800LL) return;
    struct timeval value = {.tv_sec = epoch};
    settimeofday(&value, NULL);
    if (rtc_ready) {
        struct tm local;
        localtime_r(&epoch, &local);
        pcf85063a_datetime_t date = {.year = local.tm_year + 1900, .month = local.tm_mon + 1,
            .day = local.tm_mday, .dotw = local.tm_wday, .hour = local.tm_hour, .min = local.tm_min, .sec = local.tm_sec};
        if (pcf85063a_set_time_date(&rtc, date) != ESP_OK) ESP_LOGW(TAG, "RTC write failed");
    }
}

void badge_dashboard_motion(bool playing) { moving = playing; }
void badge_dashboard_pending(bool value) { pending = value; }
void badge_dashboard_dizzy(bool value, bool recovery) { dizzy = value; recovering = recovery; }
uint32_t badge_dashboard_revision(void) { return atomic_load_explicit(&revision,memory_order_relaxed); }
void badge_dashboard_connection(bool value)
{
    portENTER_CRITICAL(&state_mux); state.connected = value; portEXIT_CRITICAL(&state_mux);
}

/* 语音状态：由 voice_client 调用。
 * 0 未启用 / 1 连接中 / 2 待命 / 3 录音 / 4 播放
 * 只写 state，不直接碰 LVGL —— 刷新由 refresh 定时器统一处理，
 * 避免跨任务操作控件导致显示锁竞争。 */
void badge_dashboard_voice(int voice_state)
{
    portENTER_CRITICAL(&state_mux);
    if (state.voice_state != voice_state) {
        state.voice_state = voice_state;
        ++revision;
    }
    portEXIT_CRITICAL(&state_mux);
}

void badge_dashboard_weather(float temperature, int code, bool day, time_t observed)
{
    portENTER_CRITICAL(&state_mux);
    state.temperature = temperature; state.code = code; state.day = day;
    state.observed = observed; state.weather_valid = true;
    portEXIT_CRITICAL(&state_mux);
}

void badge_dashboard_status(void)
{
    time_t now = time(NULL);
    char date[32];
    struct tm local;
    localtime_r(&now, &local);
    strftime(date, sizeof(date), "%Y-%m-%d %H:%M:%S", &local);
    portENTER_CRITICAL(&state_mux);
    bool connected = state.connected, valid = state.weather_valid;
    float temp = state.temperature;
    int code = state.code;
    time_t observed = state.observed;
    portEXIT_CRITICAL(&state_mux);
    unsigned glyphs = 0;
    const uint32_t characters[] = {BADGE_CHARACTER_GLYPHS};
    for (unsigned i = 0; i < sizeof(characters)/sizeof(characters[0]); ++i) {
        lv_font_glyph_dsc_t descriptor;
        if (lv_font_get_glyph_dsc(&badge_name_20, &descriptor, characters[i], 0)) ++glyphs;
    }
    printf("BADGE_DASHBOARD {\"wifi\":%s,\"time_valid\":%s,\"time\":\"%s\",\"rtc\":%s,"
           "\"city\":\"Shenzhen\",\"weather_valid\":%s,\"temperature\":%.1f,\"weather_code\":%d,"
           "\"observed\":%" PRId64 ",\"name_glyphs\":%u}\n", connected ? "true" : "false",
           badge_dashboard_time_valid() ? "true" : "false", date, rtc_ready ? "true" : "false",
           valid ? "true" : "false", temp, code, (int64_t)observed, glyphs);
}

void badge_dashboard_snapshot(void)
{
    bsp_display_lock(-1);
    lv_obj_update_layout(lv_screen_active());
    lv_draw_buf_t *snapshot = lv_snapshot_take(lv_screen_active(), LV_COLOR_FORMAT_RGB565);
    bsp_display_unlock();
    if (!snapshot) { printf("BADGE_ERROR snapshot allocation failed\n"); return; }
    printf("BADGE_SNAPSHOT %u %u %u %u\n", (unsigned)snapshot->header.w, (unsigned)snapshot->header.h,
           (unsigned)snapshot->header.stride, (unsigned)snapshot->data_size);
    fflush(stdout);
    size_t offset = 0;
    while (offset < snapshot->data_size) {
        size_t remaining = snapshot->data_size - offset;
        size_t chunk = remaining < 512 ? remaining : 512;
        int sent = usb_serial_jtag_write_bytes(snapshot->data + offset, chunk, pdMS_TO_TICKS(2000));
        if (sent <= 0) break;
        offset += sent;
    }
    printf("\nBADGE_SNAPSHOT_END\n");
    lv_draw_buf_destroy(snapshot);
}
