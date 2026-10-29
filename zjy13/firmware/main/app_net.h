#pragma once

/* Wi-Fi 连接与 WebSocket 客户端封装。 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

/* WebSocket 收到控制消息的回调 */
typedef void (*app_ws_text_cb_t)(const char *text, size_t len, void *user);

/* WebSocket 收到二进制帧的回调 */
typedef void (*app_ws_bin_cb_t)(const uint8_t *data, size_t len, void *user);

/* 连接状态变化回调 */
typedef void (*app_ws_state_cb_t)(bool connected, void *user);

/**
 * @brief 初始化 Wi-Fi 并连接。
 *
 * @param ssid      SSID，不能为空
 * @param password  密码，开放网络传 ""
 * @param timeout_ms 连接超时
 *
 * @return ESP_OK 成功
 */
esp_err_t app_net_wifi_init(const char *ssid, const char *password,
                            uint32_t timeout_ms);

/** @brief Wi-Fi 是否已连上并获得 IP。 */
bool app_net_wifi_ready(void);

/** @brief 当前 IP 字符串（未连接时返回空串）。 */
const char *app_net_ip(void);

/**
 * @brief 启动 WebSocket 客户端。
 *
 * 内部会自己管理重连。断线后按 1s/2s/4s/8s 退避重试。
 *
 * @param host      服务端地址
 * @param port      端口
 * @param path      路径，通常 "/"
 */
esp_err_t app_ws_start(const char *host, int port, const char *path);

/** @brief 注册回调。必须在 app_ws_start 之前调用。 */
void app_ws_set_callbacks(app_ws_text_cb_t on_text,
                          app_ws_bin_cb_t on_bin,
                          app_ws_state_cb_t on_state,
                          void *user);

/** @brief 是否已连上。 */
bool app_ws_connected(void);

/**
 * @brief 发送一段 JSON 文本。
 *
 * 线程安全（内部加锁）。
 *
 * @return ESP_OK 成功；ESP_ERR_INVALID_STATE 未连接
 */
esp_err_t app_ws_send_text(const char *text, size_t len);

/**
 * @brief 发送一个二进制帧。
 *
 * @return ESP_OK 成功；ESP_ERR_INVALID_STATE 未连接
 */
esp_err_t app_ws_send_bin(const uint8_t *data, size_t len);

/** @brief 主动断开。**永久停止**，重连任务会退出。 */
void app_ws_stop(void);

/** @brief 触发一次重连（保持重连任务运行，按退避重试）。 */
void app_ws_restart(void);
