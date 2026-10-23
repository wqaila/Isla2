#pragma once

#include "app_protocol.h"

/*
 * 屏幕渲染接口。
 *
 * 第一版屏幕只负责输出，不做输入。实现层应基于板子匹配的官方 BSP
 * 与 LVGL，不要自己重写 ST77916 / CST816S 驱动。
 */

/* 初始化显示，失败返回 false。 */
bool app_ui_init(void);

/* 切换主状态并渲染标题。 */
void app_ui_set_state(app_state_t state);

/* 显示用户刚发送的一句话（发送后立即调用）。 */
void app_ui_show_user(const char *text);

/* 显示角色回答，支持超过一屏时的滚动。 */
void app_ui_show_answer(const char *text);

/* 显示错误提示与可读原因。 */
void app_ui_show_error(app_error_t error);

/* 显示设置页：Wi-Fi、网关地址、网关是否就绪、角色版本。 */
void app_ui_show_settings(const char *wifi_ssid, const char *gateway_url,
                          bool gateway_ready, const char *character_version);

/* 向上/向下滚动答案区域。返回是否真的滚动了。 */
bool app_ui_scroll(int delta_lines);

/* 主循环钩子，驱动 LVGL 刷新。 */
void app_ui_tick(void);
