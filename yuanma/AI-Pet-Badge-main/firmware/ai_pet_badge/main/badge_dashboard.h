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
void badge_dashboard_weather(float temperature, int code, bool day, time_t observed);
void badge_dashboard_set_time(time_t epoch);
bool badge_dashboard_time_valid(void);
void badge_dashboard_status(void);
void badge_dashboard_snapshot(void);
void badge_network_start(void);
