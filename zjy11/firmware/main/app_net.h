#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "app_protocol.h"

/*
 * HTTP 客户端封装。
 *
 * 设计约束：
 *   - 只做单向短请求，不保存云端密钥；
 *   - 连接复用一次请求一个，避免长连接占内存；
 *   - 所有响应写入固定大小缓冲区，超出即判失败，避免堆无界增长。
 */

/* 初始化网络栈（Wi-Fi 由 app_net 内部完成连接）。 */
bool app_net_init(const char *ssid, const char *password);

/* 是否已连上 Wi-Fi 并解析到 IP。 */
bool app_net_ready(void);

/* 探测网关 /health。ready_out 为 true 表示网关已可调用云端。 */
bool app_net_health(const char *base_url, const char *token, bool *ready_out);

/*
 * 发起一轮对话。
 * 成功（HTTP 200 且 reply 非空）返回 true 并填充 reply；
 * 失败返回 false，reply->error 说明原因。
 */
bool app_net_chat(const char *base_url, const char *token,
                  const char *request_body,
                  app_reply_t *reply);

/* 清空当前会话的短期历史。 */
bool app_net_reset_session(const char *base_url, const char *token,
                           const char *session_id);
