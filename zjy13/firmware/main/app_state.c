/* 对话状态机实现。 */

#include "app_state.h"

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "app_state";

static volatile app_state_t s_state = APP_STATE_IDLE;
static SemaphoreHandle_t s_lock;

/* 迁移表：当前状态 → 允许进入的状态。
 * 任何状态都可以进 IDLE（重启/取消），ERROR 只能回 IDLE。 */
static bool allowed(app_state_t from, app_state_t to)
{
    if (to == APP_STATE_IDLE) {
        return true;
    }
    if (to == APP_STATE_CONNECTING) {
        return from == APP_STATE_IDLE || from == APP_STATE_ERROR;
    }
    if (to == APP_STATE_ERROR) {
        return from != APP_STATE_ERROR;
    }

    switch (from) {
    case APP_STATE_IDLE:            return to == APP_STATE_RECORDING;
    case APP_STATE_RECORDING:       return to == APP_STATE_ASR_PROCESSING;
    case APP_STATE_ASR_PROCESSING:  return to == APP_STATE_LLM_PROCESSING;
    case APP_STATE_LLM_PROCESSING:  return to == APP_STATE_TTS_PROCESSING;
    case APP_STATE_TTS_PROCESSING:  return to == APP_STATE_PLAYING;
    case APP_STATE_PLAYING:         return false;   /* 只能回 IDLE */
    default:                        return false;
    }
}

void app_state_init(void)
{
    if (s_lock == NULL) {
        s_lock = xSemaphoreCreateMutex();
    }
    s_state = APP_STATE_IDLE;
}

app_state_t app_state_get(void)
{
    return s_state;
}

const char *app_state_name(app_state_t st)
{
    switch (st) {
    case APP_STATE_IDLE:            return "IDLE";
    case APP_STATE_CONNECTING:      return "CONNECTING";
    case APP_STATE_RECORDING:       return "RECORDING";
    case APP_STATE_ASR_PROCESSING:  return "ASR_PROCESSING";
    case APP_STATE_LLM_PROCESSING:  return "LLM_PROCESSING";
    case APP_STATE_TTS_PROCESSING:  return "TTS_PROCESSING";
    case APP_STATE_PLAYING:         return "PLAYING";
    case APP_STATE_ERROR:           return "ERROR";
    default:                        return "UNKNOWN";
    }
}

bool app_state_try_enter(app_state_t next)
{
    if (s_lock) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
    }
    const app_state_t cur = s_state;
    const bool ok = allowed(cur, next);
    if (ok) {
        s_state = next;
        ESP_LOGI(TAG, "%s -> %s", app_state_name(cur), app_state_name(next));
    } else {
        ESP_LOGW(TAG, "拒绝迁移 %s -> %s", app_state_name(cur),
                 app_state_name(next));
    }
    if (s_lock) {
        xSemaphoreGive(s_lock);
    }
    return ok;
}

void app_state_reset(void)
{
    if (s_lock) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
    }
    const app_state_t cur = s_state;
    s_state = APP_STATE_IDLE;
    if (s_lock) {
        xSemaphoreGive(s_lock);
    }
    if (cur != APP_STATE_IDLE) {
        ESP_LOGI(TAG, "%s -> IDLE", app_state_name(cur));
    }
}

bool app_state_busy(void)
{
    const app_state_t st = s_state;
    return st == APP_STATE_RECORDING ||
           st == APP_STATE_ASR_PROCESSING ||
           st == APP_STATE_LLM_PROCESSING ||
           st == APP_STATE_TTS_PROCESSING ||
           st == APP_STATE_PLAYING;
}
