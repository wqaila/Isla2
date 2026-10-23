#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "app_limits.h"

/* 界面主状态：同一时刻只有一个，避免用户不知道设备在做什么。 */
typedef enum {
    APP_STATE_BOOT = 0,     /* 启动自检 */
    APP_STATE_CONNECTING,   /* 连接 Wi-Fi */
    APP_STATE_READY,        /* 待机，等待串口输入 */
    APP_STATE_SENT,         /* 已发送，等待云端 */
    APP_STATE_ANSWER,       /* 显示角色回答 */
    APP_STATE_ERROR,        /* 可读错误 */
    APP_STATE_SETTINGS      /* Wi-Fi / 网关 / 角色版本 */
} app_state_t;

/* 错误码：只做「码 -> 中文提示」映射，不解析网关的中文文案。 */
typedef enum {
    APP_ERROR_NONE = 0,
    APP_ERROR_NETWORK,            /* 连不上网关 */
    APP_ERROR_TIMEOUT,            /* 超时 */
    APP_ERROR_UNAUTHORIZED,       /* unauthorized */
    APP_ERROR_SESSION_NOT_FOUND,  /* session_not_found */
    APP_ERROR_REQUEST_CONFLICT,   /* request_pending / request_id_conflict */
    APP_ERROR_BUSY,               /* gateway_busy / rate_limited */
    APP_ERROR_INVALID_INPUT,      /* invalid_request / request_too_large */
    APP_ERROR_CLOUD_DISABLED,     /* cloud_disabled */
    APP_ERROR_CLOUD_NOT_CONFIGURED, /* cloud_not_configured */
    APP_ERROR_CLOUD_FAILED,       /* 其余 5xx / 解析失败 */
    APP_ERROR_LOCAL               /* 本地输入过长等 */
} app_error_t;

/* 一轮对话的结果。 */
typedef struct {
    char   reply[APP_MAX_ANSWER_CHARS + 1];
    char   session_id[APP_SESSION_ID_CHARS + 1];
    size_t reply_len;
    int    http_status;
    app_error_t error;
} app_reply_t;

/* 网关错误码字符串 -> 固件错误枚举。未知码归入云端失败。 */
app_error_t app_map_error_code(const char *code);

/* 错误枚举 -> 面向用户的短中文提示（写入 out）。 */
const char *app_error_text(app_error_t error);

/* 状态枚举 -> 屏幕标题 */
const char *app_state_title(app_state_t state);
