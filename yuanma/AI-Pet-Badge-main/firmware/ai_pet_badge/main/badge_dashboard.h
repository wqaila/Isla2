#pragma once
#include <stdbool.h>
#include <time.h>
#include "lvgl.h"

void badge_dashboard_create(lv_obj_t *screen);
void badge_dashboard_start(void);
void badge_dashboard_motion(bool playing);
void badge_dashboard_pending(bool pending);
void badge_dashboard_dizzy(bool dizzy, bool recovering);
uint32_t badge_dashboard_revision(void); /* Atomic hint; widget reads still require display lock. */
void badge_dashboard_connection(bool connected);
/* 语音状态：0 未启用 / 1 连接中 / 2 待命 / 3 录音 / 4 播放 */
void badge_dashboard_voice(int voice_state);
/* 播放音量（0~100），显示在语音状态行 */
void badge_dashboard_volume(int volume);
void badge_dashboard_weather(float temperature, int code, bool day, time_t observed);
/* 天气连续失败次数：0 表示正常。用于区分"没联网"与"联网了但天气拉不到"，
 * 避免天气一直不显示时被误判成 WiFi 没连上。 */
void badge_dashboard_weather_failed(unsigned consecutive_failures);
void badge_dashboard_set_time(time_t epoch);
bool badge_dashboard_time_valid(void);
void badge_dashboard_status(void);
void badge_dashboard_snapshot(void);
void badge_network_start(void);
