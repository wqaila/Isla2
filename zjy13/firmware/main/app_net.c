/* Wi-Fi 连接与 WebSocket 客户端实现。 */

#include "app_net.h"

#include <stdio.h>
#include <string.h>

#include "app_config.h"

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "esp_websocket_client.h"

static const char *TAG = "app_net";

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAILED_BIT    BIT1

/* 重连管理 */
#define WS_RECONNECT_TASK_STACK 4096
#define WS_RECONNECT_TASK_PRIO  4

static EventGroupHandle_t s_wifi_events;
static volatile bool s_wifi_ready;
static char s_ip[16];

/* WebSocket 状态 */
static esp_websocket_client_handle_t s_ws;
static SemaphoreHandle_t s_ws_lock;
static volatile bool s_ws_connected;
static volatile uint32_t s_ws_backoff_ms = APP_WS_RECONNECT_MIN_MS;

/* 连接参数，供重连任务使用 */
static char s_host[64];
static int  s_port;
static char s_path[32];
static TaskHandle_t s_reconnect_task;
static volatile bool s_running;

/* 回调 */
static app_ws_text_cb_t s_on_text;
static app_ws_bin_cb_t  s_on_bin;
static app_ws_state_cb_t s_on_state;
static void *s_cb_user;

/* ===================================================================
 * Wi-Fi
 * =================================================================== */

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data)
{
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *ev =
            (const wifi_event_sta_disconnected_t *)data;
        s_wifi_ready = false;
        s_ip[0] = '\0';
        xEventGroupClearBits(s_wifi_events, WIFI_CONNECTED_BIT);
        /* reason 201 = NO_AP_FOUND：SSID 不存在或不在 2.4GHz。 */
        ESP_LOGW(TAG, "Wi-Fi 断开，reason=%d%s", ev ? ev->reason : -1,
                 (ev && ev->reason == 201) ? "（找不到 AP：检查 SSID/2.4GHz）" : "");
        /* 主动重连：原实现只清状态，断网后不会自行恢复。
         * Wi-Fi 驱动允许在事件回调里调用 esp_wifi_connect()。 */
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *ev = (const ip_event_got_ip_t *)data;
        snprintf(s_ip, sizeof(s_ip), IPSTR, IP2STR(&ev->ip_info.ip));
        s_wifi_ready = true;
        xEventGroupSetBits(s_wifi_events, WIFI_CONNECTED_BIT);
        ESP_LOGI(TAG, "Wi-Fi 已连接，IP=%s", s_ip);
    }
}

esp_err_t app_net_wifi_init(const char *ssid, const char *password,
                            uint32_t timeout_ms)
{
    if (ssid == NULL || ssid[0] == '\0') {
        ESP_LOGE(TAG, "未配置 Wi-Fi SSID");
        return ESP_ERR_INVALID_ARG;
    }

    s_wifi_events = xEventGroupCreate();
    if (s_wifi_events == NULL) {
        return ESP_ERR_NO_MEM;
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));

    wifi_config_t wifi_cfg = {0};
    strncpy((char *)wifi_cfg.sta.ssid, ssid,
            sizeof(wifi_cfg.sta.ssid) - 1);
    if (password != NULL) {
        strncpy((char *)wifi_cfg.sta.password, password,
                sizeof(wifi_cfg.sta.password) - 1);
    }
    /* ESP32-S3 只有 2.4GHz。若配了 5GHz 的 SSID 会 reason=201。 */
    wifi_cfg.sta.threshold.authmode = (password && password[0])
                                          ? WIFI_AUTH_WPA2_PSK
                                          : WIFI_AUTH_OPEN;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "正在连接 Wi-Fi SSID=%s ...", ssid);
    const EventBits_t bits = xEventGroupWaitBits(
        s_wifi_events, WIFI_CONNECTED_BIT | WIFI_FAILED_BIT,
        pdFALSE, pdFALSE, pdMS_TO_TICKS(timeout_ms));

    if ((bits & WIFI_CONNECTED_BIT) == 0) {
        ESP_LOGE(TAG, "Wi-Fi 连接超时（%u ms）", (unsigned)timeout_ms);
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

bool app_net_wifi_ready(void)
{
    return s_wifi_ready;
}

const char *app_net_ip(void)
{
    return s_ip;
}

/* ===================================================================
 * WebSocket
 * =================================================================== */

/* ===================================================================
 * 消息分片重组
 *
 * 组件在 payload 超过 buffer_size 时会分多次 WEBSOCKET_EVENT_DATA 上报，
 * 原实现只处理 payload_offset==0 的首片，后续片被丢弃，大 JSON 会被截断。
 * 这里按 payload_len 做缓冲重组，超过上限则丢弃并标记错误。
 * =================================================================== */

#define WS_REASM_TEXT_MAX 16384    /* 控制消息上限，远小于协议 64KB */
#define WS_REASM_BIN_MAX  (48 * 1024)

static char  s_reasm_text[WS_REASM_TEXT_MAX];
static size_t s_reasm_text_len;
static bool   s_reasm_text_bad;

static uint8_t s_reasm_bin[WS_REASM_BIN_MAX];
static size_t  s_reasm_bin_len;
static bool    s_reasm_bin_bad;

static void reasm_reset(void)
{
    s_reasm_text_len = 0;
    s_reasm_text_bad = false;
    s_reasm_bin_len = 0;
    s_reasm_bin_bad = false;
}

static void ws_event_handler(void *arg, esp_event_base_t base,
                             int32_t event_id, void *event_data)
{
    (void)arg;
    (void)base;
    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;

    switch (event_id) {
    case WEBSOCKET_EVENT_CONNECTED:
        s_ws_connected = true;
        s_ws_backoff_ms = APP_WS_RECONNECT_MIN_MS;  /* 连上就重置退避 */
        reasm_reset();
        ESP_LOGI(TAG, "WebSocket 已连接");
        if (s_on_state) {
            s_on_state(true, s_cb_user);
        }
        break;

    case WEBSOCKET_EVENT_DISCONNECTED:
        s_ws_connected = false;
        reasm_reset();          /* 断线丢弃半截消息，避免拼到新连接里 */
        ESP_LOGW(TAG, "WebSocket 断开");
        if (s_on_state) {
            s_on_state(false, s_cb_user);
        }
        break;

    case WEBSOCKET_EVENT_DATA:
        if (data == NULL || data->data_len <= 0) {
            break;
        }

        if (data->op_code == 0x01) {
            /* 文本：按 offset 累加，收满 payload_len 才交付 */
            if (data->payload_offset == 0) {
                s_reasm_text_len = 0;
                s_reasm_text_bad = false;
            }
            if (s_reasm_text_bad) {
                break;
            }
            const size_t need = s_reasm_text_len + (size_t)data->data_len;
            if (need > sizeof(s_reasm_text) - 1) {
                ESP_LOGE(TAG, "文本消息超缓冲上限 %u，丢弃",
                         (unsigned)sizeof(s_reasm_text));
                s_reasm_text_bad = true;
                s_reasm_text_len = 0;
                break;
            }
            memcpy(s_reasm_text + s_reasm_text_len, data->data_ptr,
                   (size_t)data->data_len);
            s_reasm_text_len = need;

            if ((int)s_reasm_text_len >= data->payload_len) {
                s_reasm_text[s_reasm_text_len] = '\0';
                if (s_on_text) {
                    s_on_text(s_reasm_text, s_reasm_text_len, s_cb_user);
                }
                s_reasm_text_len = 0;
            }
        } else if (data->op_code == 0x02) {
            /* 二进制：单帧协议体最大 12 + 48KB，同样需要重组 */
            if (data->payload_offset == 0) {
                s_reasm_bin_len = 0;
                s_reasm_bin_bad = false;
            }
            if (s_reasm_bin_bad) {
                break;
            }
            const size_t need = s_reasm_bin_len + (size_t)data->data_len;
            if (need > sizeof(s_reasm_bin)) {
                ESP_LOGE(TAG, "二进制消息超缓冲上限，丢弃");
                s_reasm_bin_bad = true;
                s_reasm_bin_len = 0;
                break;
            }
            memcpy(s_reasm_bin + s_reasm_bin_len, data->data_ptr,
                   (size_t)data->data_len);
            s_reasm_bin_len = need;

            if ((int)s_reasm_bin_len >= data->payload_len) {
                if (s_on_bin) {
                    s_on_bin(s_reasm_bin, s_reasm_bin_len, s_cb_user);
                }
                s_reasm_bin_len = 0;
            }
        }
        break;

    case WEBSOCKET_EVENT_ERROR:
        ESP_LOGW(TAG, "WebSocket 错误");
        break;

    default:
        break;
    }
}

/* 重连任务：连上后阻塞等待，断开则按退避重试 */
static void ws_reconnect_task(void *arg)
{
    (void)arg;
    char uri[96];
    /* 用 snprintf 的返回值检查，避免 -Wformat-truncation 把警告当错误 */
    const int n = snprintf(uri, sizeof(uri), "ws://%s:%d%s", s_host, s_port,
                           s_path);
    if (n <= 0 || (size_t)n >= sizeof(uri)) {
        ESP_LOGE(TAG, "服务端 URI 过长: host=%s port=%d path=%s",
                 s_host, s_port, s_path);
        s_reconnect_task = NULL;
        vTaskDelete(NULL);
        return;
    }

    esp_websocket_client_config_t cfg = {
        .uri = uri,
        .reconnect_timeout_ms = 0,   /* 重连由本任务控制，不用组件内置的 */
        .network_timeout_ms = 10000,
        .buffer_size = 8192,         /* 下行帧最大 4096B + 余量 */
        .task_stack = 4096,
        .disable_auto_reconnect = true,
    };

    /* 注意：组件启动时就会发起连接，若首次失败不会自动重试
     * （disable_auto_reconnect=true），由下面的循环负责。 */
    s_ws = esp_websocket_client_init(&cfg);
    if (s_ws == NULL) {
        ESP_LOGE(TAG, "WebSocket 客户端创建失败");
        s_reconnect_task = NULL;
        vTaskDelete(NULL);
        return;
    }
    esp_websocket_register_events(s_ws, WEBSOCKET_EVENT_ANY,
                                  ws_event_handler, NULL);

    while (s_running) {
        if (!s_ws_connected) {
            if (!app_net_wifi_ready()) {
                ESP_LOGW(TAG, "Wi-Fi 未就绪，等待后重试");
                vTaskDelay(pdMS_TO_TICKS(2000));
                continue;
            }
            ESP_LOGI(TAG, "连接 %s ...", uri);
            esp_err_t err = esp_websocket_client_start(s_ws);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "启动失败: %s", esp_err_to_name(err));
            }
            /* 等待连接结果，最多 10 秒 */
            for (int i = 0; i < 100 && !s_ws_connected && s_running; ++i) {
                vTaskDelay(pdMS_TO_TICKS(100));
            }
            if (!s_ws_connected) {
                ESP_LOGW(TAG, "连接超时，%u ms 后重试",
                         (unsigned)s_ws_backoff_ms);
                esp_websocket_client_stop(s_ws);
                vTaskDelay(pdMS_TO_TICKS(s_ws_backoff_ms));
                s_ws_backoff_ms *= 2;
                if (s_ws_backoff_ms > APP_WS_RECONNECT_MAX_MS) {
                    s_ws_backoff_ms = APP_WS_RECONNECT_MAX_MS;
                }
                continue;
            }
            /* 已连接，等断线 */
            while (s_ws_connected && s_running) {
                vTaskDelay(pdMS_TO_TICKS(500));
            }
            if (!s_running) {
                break;
            }
            /* 断线了，关闭再重连 */
            esp_websocket_client_stop(s_ws);
            vTaskDelay(pdMS_TO_TICKS(s_ws_backoff_ms));
            s_ws_backoff_ms *= 2;
            if (s_ws_backoff_ms > APP_WS_RECONNECT_MAX_MS) {
                s_ws_backoff_ms = APP_WS_RECONNECT_MAX_MS;
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(200));
        }
    }

    esp_websocket_client_destroy(s_ws);
    s_ws = NULL;
    s_reconnect_task = NULL;
    vTaskDelete(NULL);
}

void app_ws_set_callbacks(app_ws_text_cb_t on_text,
                          app_ws_bin_cb_t on_bin,
                          app_ws_state_cb_t on_state,
                          void *user)
{
    s_on_text = on_text;
    s_on_bin = on_bin;
    s_on_state = on_state;
    s_cb_user = user;
}

esp_err_t app_ws_start(const char *host, int port, const char *path)
{
    if (host == NULL || host[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_reconnect_task != NULL) {
        return ESP_OK;   /* 已启动 */
    }

    snprintf(s_host, sizeof(s_host), "%s", host);
    s_port = port;
    snprintf(s_path, sizeof(s_path), "%s", path ? path : "/");

    s_ws_lock = xSemaphoreCreateMutex();
    if (s_ws_lock == NULL) {
        return ESP_ERR_NO_MEM;
    }
    s_running = true;

    xTaskCreate(ws_reconnect_task, "ws_reconnect",
                WS_RECONNECT_TASK_STACK, NULL, WS_RECONNECT_TASK_PRIO,
                &s_reconnect_task);
    return s_reconnect_task ? ESP_OK : ESP_ERR_NO_MEM;
}

bool app_ws_connected(void)
{
    return s_ws_connected;
}

esp_err_t app_ws_send_text(const char *text, size_t len)
{
    if (text == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_ws_connected || s_ws == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    /* 串行化发送：同时从 UI 任务和音频任务发消息会打乱帧序 */
    if (s_ws_lock) {
        xSemaphoreTake(s_ws_lock, portMAX_DELAY);
    }
    const int ret = esp_websocket_client_send_text(s_ws, text, (int)len,
                                                   pdMS_TO_TICKS(1000));
    if (s_ws_lock) {
        xSemaphoreGive(s_ws_lock);
    }

    if (ret < 0) {
        ESP_LOGW(TAG, "文本发送失败: %d", ret);
        return ESP_FAIL;
    }
    return ESP_OK;
}

esp_err_t app_ws_send_bin(const uint8_t *data, size_t len)
{
    if (data == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_ws_connected || s_ws == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    if (s_ws_lock) {
        xSemaphoreTake(s_ws_lock, portMAX_DELAY);
    }
    const int ret = esp_websocket_client_send_bin(s_ws, (const char *)data,
                                                  (int)len,
                                                  pdMS_TO_TICKS(1000));
    if (s_ws_lock) {
        xSemaphoreGive(s_ws_lock);
    }

    if (ret < 0) {
        ESP_LOGW(TAG, "二进制发送失败: %d", ret);
        return ESP_FAIL;
    }
    return ESP_OK;
}

void app_ws_stop(void)
{
    /* 永久停止：置 s_running=false 会让重连任务退出并销毁客户端。
     *
     * 踩坑记录：原实现把它当作"断开重连"使用，结果心跳超时后
     * 连接永久失效，日志却写着"断开重连"。
     * 需要重连的场景请用 app_ws_restart()。
     */
    s_running = false;
}

void app_ws_restart(void)
{
    /* 触发重连：只停当前连接，不终止重连任务。
     * 重连任务检测到 s_ws_connected 变 false 后会按退避重新连接。 */
    if (s_ws != NULL && s_ws_connected) {
        ESP_LOGW(TAG, "主动断开以触发重连");
        esp_websocket_client_close(s_ws, pdMS_TO_TICKS(1000));
    }
}
