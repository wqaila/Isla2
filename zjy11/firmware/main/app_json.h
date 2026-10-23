#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "app_limits.h"
#include "app_protocol.h"

/*
 * 与网关的 JSON 交互。
 *
 * 第一版刻意不用第三方 JSON 库：请求结构固定，回答只需要取
 * reply / session_id / error.code 三个字段，手写解析足够且便于审计。
 * 所有输出都受 app_limits.h 的大小限制约束。
 */

/*
 * 构造 /v1/chat 请求体。
 *
 * request_id 必填（每轮新建）；session_id 可为 NULL（首轮）。
 * 返回写入 out 的字节数，0 表示参数不合法或缓冲区不足。
 */
size_t app_build_chat_request(const char *request_id,
                              const char *session_id,
                              const char *message,
                              char *out, size_t out_size);

/* 解析 /v1/chat 成功响应。返回 true 表示拿到非空 reply。 */
bool app_parse_chat_reply(const char *body, size_t len, app_reply_t *reply);

/* 解析网关错误响应中的 error.code（没有则返回 APP_ERROR_CLOUD_FAILED）。 */
app_error_t app_parse_error_body(const char *body, size_t len);

/* 供测试使用的转义长度估算：转义后需要的字节数（含结尾 0）。 */
size_t app_json_escaped_size(const char *text);
