#include "app_protocol.h"

#include <string.h>

app_error_t app_map_error_code(const char *code)
{
    if (code == NULL) {
        return APP_ERROR_CLOUD_FAILED;
    }

    struct {
        const char *code;
        app_error_t error;
    } table[] = {
        {"unauthorized",             APP_ERROR_UNAUTHORIZED},
        {"session_not_found",        APP_ERROR_SESSION_NOT_FOUND},
        {"request_pending",          APP_ERROR_REQUEST_CONFLICT},
        {"request_id_conflict",      APP_ERROR_REQUEST_CONFLICT},
        {"session_reset",            APP_ERROR_REQUEST_CONFLICT},
        {"gateway_busy",             APP_ERROR_BUSY},
        {"rate_limited",             APP_ERROR_BUSY},
        {"request_cache_full",       APP_ERROR_BUSY},
        {"session_limit",            APP_ERROR_BUSY},
        {"invalid_request",          APP_ERROR_INVALID_INPUT},
        {"request_too_large",        APP_ERROR_INVALID_INPUT},
        {"response_too_large",       APP_ERROR_CLOUD_FAILED},
        {"cloud_disabled",           APP_ERROR_CLOUD_DISABLED},
        {"cloud_not_configured",     APP_ERROR_CLOUD_NOT_CONFIGURED},
        {"cloud_auth_failed",        APP_ERROR_CLOUD_FAILED},
        {"cloud_rate_limited",       APP_ERROR_CLOUD_FAILED},
        {"cloud_http_error",         APP_ERROR_CLOUD_FAILED},
        {"cloud_timeout",            APP_ERROR_TIMEOUT},
        {"cloud_network_error",      APP_ERROR_NETWORK},
        {"cloud_response_invalid",   APP_ERROR_CLOUD_FAILED},
        {"cloud_response_too_large", APP_ERROR_CLOUD_FAILED},
        {"cloud_reply_truncated",    APP_ERROR_CLOUD_FAILED},
        {"cloud_reply_invalid",      APP_ERROR_CLOUD_FAILED},
        {"cloud_reply_incomplete",   APP_ERROR_CLOUD_FAILED},
        {"request_interrupted",      APP_ERROR_CLOUD_FAILED},
    };

    for (size_t i = 0; i < sizeof(table) / sizeof(table[0]); ++i) {
        if (strcmp(code, table[i].code) == 0) {
            return table[i].error;
        }
    }
    return APP_ERROR_CLOUD_FAILED;
}

const char *app_error_text(app_error_t error)
{
    switch (error) {
    case APP_ERROR_NONE:                 return "";
    case APP_ERROR_NETWORK:              return "连不上网关，请检查网络";
    case APP_ERROR_TIMEOUT:              return "网关响应超时，请重试";
    case APP_ERROR_UNAUTHORIZED:         return "设备令牌无效，请检查配置";
    case APP_ERROR_SESSION_NOT_FOUND:    return "会话已过期，将开始新会话";
    case APP_ERROR_REQUEST_CONFLICT:     return "本次请求已处理过，请重新输入";
    case APP_ERROR_BUSY:                 return "网关正忙，请稍后再试";
    case APP_ERROR_INVALID_INPUT:        return "输入过长或格式不对";
    case APP_ERROR_CLOUD_DISABLED:       return "网关未开启云端调用";
    case APP_ERROR_CLOUD_NOT_CONFIGURED: return "网关尚未配置云端模型";
    case APP_ERROR_CLOUD_FAILED:         return "云端回答失败，请稍后重试";
    case APP_ERROR_LOCAL:                return "本地错误";
    default:                             return "未知错误";
    }
}

const char *app_state_title(app_state_t state)
{
    switch (state) {
    case APP_STATE_BOOT:       return "启动中";
    case APP_STATE_CONNECTING: return "连接网络";
    case APP_STATE_READY:      return "等待输入";
    case APP_STATE_SENT:       return "思考中";
    case APP_STATE_ANSWER:     return "爱莉希雅";
    case APP_STATE_ERROR:      return "出错了";
    case APP_STATE_SETTINGS:   return "设置";
    default:                   return "";
    }
}
