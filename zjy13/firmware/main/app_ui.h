#pragma once

/* 屏幕显示（ST77916 QSPI 360x360）。 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

/**
 * @brief 初始化 LCD（含 ST77916 初始化序列与帧缓冲）。
 *
 * 用 PSRAM 做帧缓冲：360x360 RGB565 = 253KB，
 * 内部 SRAM 放不下（语音缓冲还要占一部分）。
 */
esp_err_t app_ui_init(void);

/** @brief 设置连接状态行（顶部）。 */
void app_ui_set_status(const char *text);

/** @brief 显示识别到的用户文本（中部）。 */
void app_ui_set_asr(const char *text);

/**
 * @brief 追加/更新角色回复（下部）。
 *
 * 流式调用：每收到一个 llm_delta 就追加一段。
 */
void app_ui_append_reply(const char *delta);

/** @brief 清空回复区（新一轮开始时）。 */
void app_ui_clear_reply(void);

/** @brief 显示一个瞬时提示（如"录音中""播放中"）。 */
void app_ui_set_hint(const char *text);

/** @brief 显示错误（红色，带错误码）。 */
void app_ui_show_error(const char *code, const char *message);

/** @brief 强制刷新（正常情况下各 setter 会自动刷新）。 */
void app_ui_flush(void);
