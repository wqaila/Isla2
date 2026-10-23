#include "app_net.h"

#include <string.h>

#include "app_config.h"
#include "app_json.h"
#include "app_limits.h"

#include "esp_crt_bundle.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

static const char *TAG = "app_net";

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAILED_BIT    BIT1

static EventGroupHandle_t s_wifi_events;
static bool s_wifi_ready;

/* 响应累积缓冲区：固定大小，超出即判失败，避免无界增长。 */
typedef struct {
    char  *buf;
    size_t cap;
    size_t len;
    bool   overflow;
} http_sink_t;

static esp_err_t http_event(esp_http_client_event_t *evt)
{
    http_sink_t *sink = (http_sink_t *)evt->user_data;
    if (sink == NULL) {
        return ESP_OK;
    }
    if (evt->event_id == HTTP_EVENT_ON_DATA && evt->data != NULL && evt->data_len > 0) {
        if (sink->len + (size_t)evt->data_len + 1 > sink->cap) {
            sink->overflow = true;
            return ESP_FAIL;
        }
        memcpy(sink->buf + sink->len, evt->data, (size_t)evt->data_len);
        sink->len += (size_t)evt->data_len;
        sink->buf[sink->len] = '\0';
    }
    return ESP_OK;
}

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data)
{
    (void)arg;
    (void)data;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_wifi_ready = false;
        xEventGroupClearBits(s_wifi_events, WIFI_CONNECTED_BIT);
        /* 不在事件里死循环重连，交给上层下次请求前重试。 */
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_wifi_ready = true;
        xEventGroupSetBits(s_wifi_events, WIFI_CONNECTED_BIT);
    }
}

bool app_net_init(const char *ssid, const char *password)
{
    if (ssid == NULL || ssid[0] == '\0') {
        ESP_LOGE(TAG, "未配置 Wi-Fi 名称");
        return false;
    }

    s_wifi_events = xEventGroupCreate();
    if (s_wifi_events == NULL) {
        return false;
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

    wifi_config_t wifi_config = {0};
    strncpy((char *)wifi_config.sta.ssid, ssid, sizeof(wifi_config.sta.ssid) - 1);
    if (password != NULL) {
        strncpy((char *)wifi_config.sta.password, password,
                sizeof(wifi_config.sta.password) - 1);
    }
    /* 第一版只支持 2.4 GHz，避免用户配成 5 GHz 后连不上。 */
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    const EventBits_t bits = xEventGroupWaitBits(
        s_wifi_events, WIFI_CONNECTED_BIT | WIFI_FAILED_BIT,
        pdFALSE, pdFALSE, pdMS_TO_TICKS(20000));

    if ((bits & WIFI_CONNECTED_BIT) == 0) {
        ESP_LOGE(TAG, "Wi-Fi 连接超时");
        return false;
    }
    ESP_LOGI(TAG, "Wi-Fi 已连接");
    return true;
}

bool app_net_ready(void)
{
    return s_wifi_ready;
}

/* 发起一次 GET/POST，把响应读进 sink。返回 HTTP 状态码，负数表示失败。 */
static int http_perform(const char *base_url, const char *path,
                        const char *token, const char *body,
                        char *out, size_t out_size, size_t *out_len)
{
    if (base_url == NULL || token == NULL || out == NULL || out_size == 0) {
        return -1;
    }

    char url[APP_MAX_BASE_URL_CHARS + 64];
    const int n = snprintf(url, sizeof(url), "%s%s", base_url, path);
    if (n <= 0 || (size_t)n >= sizeof(url)) {
        return -1;
    }

    http_sink_t sink = {.buf = out, .cap = out_size, .len = 0, .overflow = false};
    out[0] = '\0';

    char auth[APP_MAX_TOKEN_CHARS + 16];
    const int an = snprintf(auth, sizeof(auth), "Bearer %s", token);
    if (an <= 0 || (size_t)an >= sizeof(auth)) {
        return -1;
    }

    esp_http_client_config_t cfg = {
        .url = url,
        .event_handler = http_event,
        .user_data = &sink,
        .timeout_ms = APP_HTTP_TIMEOUT_MS,
        .crt_bundle_attach = esp_crt_bundle_attach,
        /* 第一版是局域网明文 HTTP；若改成 HTTPS 必须保留证书校验。 */
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (client == NULL) {
        return -1;
    }

    esp_http_client_set_header(client, "Authorization", auth);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_header(client, "Accept", "application/json");

    int status = -1;
    bool sent = false;
    if (body != NULL) {
        esp_http_client_set_method(client, HTTP_METHOD_POST);
        esp_http_client_set_post_field(client, body, (int)strlen(body));
        if (esp_http_client_perform(client) == ESP_OK) {
            status = esp_http_client_get_status_code(client);
            sent = true;
        }
    } else {
        esp_http_client_set_method(client, HTTP_METHOD_GET);
        if (esp_http_client_perform(client) == ESP_OK) {
            status = esp_http_client_get_status_code(client);
            sent = true;
        }
    }

    esp_http_client_cleanup(client);

    if (!sent || sink.overflow) {
        return -1;
    }
    if (out_len != NULL) {
        *out_len = sink.len;
    }
    return status;
}

bool app_net_health(const char *base_url, const char *token, bool *ready_out)
{
    char body[1024];
    size_t len = 0;
    if (ready_out != NULL) {
        *ready_out = false;
    }
    const int status = http_perform(base_url, "/health", token, NULL,
                                    body, sizeof(body), &len);
    if (status != 200) {
        return false;
    }
    if (ready_out != NULL) {
        *ready_out = (strstr(body, "\"ready\":true") != NULL ||
                      strstr(body, "\"ready\": true") != NULL);
    }
    return true;
}

bool app_net_chat(const char *base_url, const char *token,
                  const char *request_body,
                  app_reply_t *reply)
{
    if (reply == NULL || request_body == NULL) {
        return false;
    }
    memset(reply, 0, sizeof(*reply));

    char body[APP_MAX_RESPONSE_BYTES + 1];
    size_t len = 0;
    const int status = http_perform(base_url, "/v1/chat", token, request_body,
                                    body, sizeof(body), &len);
    reply->http_status = status;

    if (status < 0) {
        reply->error = APP_ERROR_NETWORK;
        return false;
    }
    if (status == 200) {
        if (app_parse_chat_reply(body, len, reply)) {
            return true;
        }
        reply->error = APP_ERROR_CLOUD_FAILED;
        return false;
    }

    reply->error = app_parse_error_body(body, len);
    return false;
}

bool app_net_reset_session(const char *base_url, const char *token,
                           const char *session_id)
{
    if (session_id == NULL || session_id[0] == '\0') {
        return false;
    }
    char body[APP_SESSION_ID_CHARS + 32];
    const int n = snprintf(body, sizeof(body),
                           "{\"session_id\":\"%s\"}", session_id);
    if (n <= 0 || (size_t)n >= sizeof(body)) {
        return false;
    }

    char out[512];
    size_t len = 0;
    const int status = http_perform(base_url, "/v1/session/reset", token, body,
                                    out, sizeof(out), &len);
    return status == 200;
}
