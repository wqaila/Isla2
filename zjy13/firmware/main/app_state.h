#pragma once

/*
 * 对话状态机。
 *
 * 与服务端 server/ws/states.py 对齐：
 *   IDLE → RECORDING → ASR_PROCESSING → LLM_PROCESSING
 *        → TTS_PROCESSING → PLAYING → IDLE
 *
 * 半双工约束（V2 第九节）：RECORDING 及之后都不接受新的开始录音，
 * 服务端会返回 turn_busy。固件侧也要自己拦住，不要发无意义的请求。
 */

#include <stdbool.h>

typedef enum {
    APP_STATE_IDLE = 0,
    APP_STATE_CONNECTING,
    APP_STATE_RECORDING,
    APP_STATE_ASR_PROCESSING,
    APP_STATE_LLM_PROCESSING,
    APP_STATE_TTS_PROCESSING,
    APP_STATE_PLAYING,
    APP_STATE_ERROR,
} app_state_t;

/** @brief 初始化状态机。 */
void app_state_init(void);

/** @brief 当前状态。 */
app_state_t app_state_get(void);

/** @brief 状态名（日志/显示用）。 */
const char *app_state_name(app_state_t st);

/**
 * @brief 尝试迁移到新状态。
 *
 * @return true 允许迁移；false 拒绝（调用方应回 turn_busy 或忽略）
 */
bool app_state_try_enter(app_state_t next);

/** @brief 回到 IDLE（唯一允许从任何状态进入的状态）。 */
void app_state_reset(void);

/** @brief 当前是否忙（RECORDING 及之后的状态）。 */
bool app_state_busy(void);
