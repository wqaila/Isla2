#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "badge_dashboard.h"
#include "wifi_config.h"
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_sntp.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "badge_net";
static volatile bool connected;
static TaskHandle_t network_task;
static char response[4096];
static size_t response_size;

static void on_time_sync(struct timeval *now)
{
    badge_dashboard_set_time(now->tv_sec);
    ESP_LOGI(TAG, "Network time synchronized");
}

static void network_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *ev = (const ip_event_got_ip_t *)data;
        connected = true;
        badge_dashboard_connection(true);
        /* 打印实际 IP：板子与电脑是否同网段，靠这行判断。
         * 没有这行就只能盲猜网段，联调时会浪费很多时间。 */
        ESP_LOGI(TAG, "Wi-Fi connected  IP=" IPSTR "  GW=" IPSTR,
                 IP2STR(&ev->ip_info.ip), IP2STR(&ev->ip_info.gw));
        printf("BADGE_NET {\"ip\":\"" IPSTR "\",\"gw\":\"" IPSTR "\"}\n",
               IP2STR(&ev->ip_info.ip), IP2STR(&ev->ip_info.gw));
        if (network_task) xTaskNotifyGive(network_task);
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        connected = false;
        badge_dashboard_connection(false);
        wifi_event_sta_disconnected_t *event = (wifi_event_sta_disconnected_t *)data;
        ESP_LOGW(TAG, "Wi-Fi disconnected, reason %u", (unsigned)event->reason);
        /* 主动重连：否则断网后不会自行恢复 */
        esp_wifi_connect();
    }
}

static esp_err_t on_http(esp_http_client_event_t *event)
{
    if (event->event_id == HTTP_EVENT_ON_DATA) {
        if (event->data_len < 0 || response_size + event->data_len >= sizeof(response)) return ESP_ERR_NO_MEM;
        memcpy(response + response_size, event->data, event->data_len);
        response_size += event->data_len;
        response[response_size] = 0;
    }
    return ESP_OK;
}

static bool fetch_weather(void)
{
    // Shenzhen city centre, WGS84. Only current model data is requested.
    esp_http_client_config_t config = {
        .url = "https://api.open-meteo.com/v1/forecast?latitude=22.5431&longitude=114.0579"
               "&current=temperature_2m,weather_code,is_day&timeformat=unixtime&timezone=Asia%2FShanghai&forecast_days=1",
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 12000,
        .buffer_size = 2048,
        .event_handler = on_http,
    };
    response_size = 0;
    response[0] = 0;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) return false;
    esp_err_t error = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);
    if (error != ESP_OK || status != 200 || !response_size) {
        ESP_LOGW(TAG, "Weather fetch failed: %s, HTTP %d", esp_err_to_name(error), status);
        return false;
    }
    cJSON *json = cJSON_Parse(response);
    cJSON *current = cJSON_GetObjectItemCaseSensitive(json, "current");
    cJSON *temp = cJSON_GetObjectItemCaseSensitive(current, "temperature_2m");
    cJSON *code = cJSON_GetObjectItemCaseSensitive(current, "weather_code");
    cJSON *day = cJSON_GetObjectItemCaseSensitive(current, "is_day");
    cJSON *when = cJSON_GetObjectItemCaseSensitive(current, "time");
    bool valid = cJSON_IsNumber(temp) && cJSON_IsNumber(code) && cJSON_IsNumber(day) && cJSON_IsNumber(when);
    time_t now = time(NULL);
    if (valid) valid = code->valueint >= 0 && code->valueint <= 99 &&
                       temp->valuedouble >= -60 && temp->valuedouble <= 60 &&
                       when->valuedouble <= now + 900 && when->valuedouble >= now - 7200;
    if (valid) {
        badge_dashboard_weather(temp->valuedouble, code->valueint, day->valueint != 0, (time_t)when->valuedouble);
        ESP_LOGI(TAG, "Shenzhen weather updated: %.1f C, code %d", temp->valuedouble, code->valueint);
    } else ESP_LOGW(TAG, "Weather response invalid or stale");
    cJSON_Delete(json);
    return valid;
}

static void run_network(void *arg)
{
    (void)arg;
    bool sntp_started = false;
    int64_t next_connect = 0, next_weather = 0;
    while (true) {
        int64_t now = esp_timer_get_time();
        if (!connected) {
            if (now >= next_connect) {
                esp_wifi_connect();
                next_connect = now + 10000000;
            }
            next_weather = 0;
        } else {
            if (!sntp_started) {
                esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
                esp_sntp_setservername(0, "ntp.aliyun.com");
                esp_sntp_set_time_sync_notification_cb(on_time_sync);
                esp_sntp_init();
                sntp_started = true;
            }
            if (badge_dashboard_time_valid() && now >= next_weather) {
                bool success = fetch_weather();
                next_weather = esp_timer_get_time() + (success ? 900LL : 60LL) * 1000000;
            }
        }
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000));
    }
}

void badge_network_start(void)
{
    if (!strlen(BADGE_WIFI_SSID)) { ESP_LOGI(TAG, "Wi-Fi unconfigured"); return; }
    ESP_ERROR_CHECK(esp_netif_init());
    esp_err_t error = esp_event_loop_create_default();
    if (error != ESP_ERR_INVALID_STATE) ESP_ERROR_CHECK(error);
    assert(esp_netif_create_default_wifi_sta());
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, network_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, network_event, NULL));
    wifi_config_t config = {0};
    memcpy(config.sta.ssid, BADGE_WIFI_SSID, strlen(BADGE_WIFI_SSID));
    memcpy(config.sta.password, BADGE_WIFI_PASSWORD, strlen(BADGE_WIFI_PASSWORD));
    config.sta.threshold.authmode = strlen(BADGE_WIFI_PASSWORD) ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    config.sta.pmf_cfg.capable = true;
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &config));
    ESP_ERROR_CHECK(esp_wifi_start());
    assert(xTaskCreate(run_network, "dashboard_net", 8192, NULL, 2, &network_task) == pdPASS);
}
