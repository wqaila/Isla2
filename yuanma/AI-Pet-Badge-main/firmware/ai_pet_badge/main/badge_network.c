#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "badge_dashboard.h"
#include "voice_client.h"
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
static esp_netif_t *sta_netif;
/* 天气连续失败次数：只用于日志与界面提示，不参与业务逻辑 */
static unsigned weather_failures;

/* Wi-Fi 断线重连的指数退避（毫秒）。
 *
 * 2026-11-23 新增。原实现在 WIFI_EVENT_STA_DISCONNECTED 里无条件立即
 * esp_wifi_connect()。实测 reason=201（NO_AP_FOUND）时会形成重试风暴：
 * 扫描→失败→立刻再扫，射频长期忙态，反而更难重新发现 AP，
 * 并挤占同芯片 WebSocket 的收发（表现为心跳超时、playback_done 丢失）。
 *
 * 500ms 起、每次翻倍、上限 8s；GOT_IP 时重置回 500ms。
 */
static uint32_t s_retry_delay_ms = 500;

/* ===== DNS 兜底 =====
 *
 * Windows ICS 的 DNS 代理（192.168.137.1:53）有一种失效状态：
 * 进程在跑、端口在监听，但对查询直接回 RST。
 * 此时 DHCP 下发的 DNS 完全不可用 —— 天气模块 getaddrinfo() 返回 202，
 * 而 WiFi 明明是连着的，屏幕上只表现为温度一直是 "--°"，
 * 极易被误判成"板子没连上 WiFi"。
 *
 * 所以在拿到 IP 后显式覆盖成公共 DNS，绕开坏掉的代理。
 * 这两个宏由 wifi_config.h 提供；没定义时用下面的默认值，
 * 保证即使生成脚本没写也能编译并生效。 */
#ifndef BADGE_DNS_MAIN
#define BADGE_DNS_MAIN "223.5.5.5"
#endif
#ifndef BADGE_DNS_BACKUP
#define BADGE_DNS_BACKUP "8.8.8.8"
#endif

/* 把当前生效的 DNS 打到日志，避免下次排查又靠猜。 */
static void log_dns_config(const char *when)
{
    if (sta_netif == NULL) return;
    esp_netif_dns_info_t main_dns = {0}, backup_dns = {0};
    esp_netif_get_dns_info(sta_netif, ESP_NETIF_DNS_MAIN, &main_dns);
    esp_netif_get_dns_info(sta_netif, ESP_NETIF_DNS_BACKUP, &backup_dns);
    ESP_LOGI(TAG, "%s DNS: main=" IPSTR " backup=" IPSTR, when,
             IP2STR(&main_dns.ip.u_addr.ip4), IP2STR(&backup_dns.ip.u_addr.ip4));
}

static bool set_dns(esp_netif_dns_type_t type, const char *addr)
{
    esp_netif_dns_info_t dns = {0};
    if (esp_netif_str_to_ip4(addr, &dns.ip.u_addr.ip4) != ESP_OK) {
        ESP_LOGW(TAG, "DNS 地址无效，跳过: %s", addr);
        return false;
    }
    dns.ip.type = ESP_IPADDR_TYPE_V4;
    esp_err_t err = esp_netif_set_dns_info(sta_netif, type, &dns);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "设置 DNS 失败(%s): %s", addr, esp_err_to_name(err));
        return false;
    }
    return true;
}

/* 覆盖 DNS。BADGE_DNS_MAIN 为空串表示不覆盖，沿用 DHCP 下发值。 */
static void apply_dns_override(void)
{
    if (sta_netif == NULL || strlen(BADGE_DNS_MAIN) == 0) return;
    if (!set_dns(ESP_NETIF_DNS_MAIN, BADGE_DNS_MAIN)) return;
    if (strlen(BADGE_DNS_BACKUP)) set_dns(ESP_NETIF_DNS_BACKUP, BADGE_DNS_BACKUP);
    log_dns_config("已覆盖为显式 DNS，");
}

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
        /* DHCP 下发的 DNS 可能指向已失效的 ICS 代理，这里覆盖掉。
         * 必须在拿到 IP 之后调用，早于此时 netif 还没有 DNS 记录。 */
        log_dns_config("DHCP 下发");
        apply_dns_override();
        /* 连上后清零退避计数，下次断线重新从 500ms 开始。
         * （变量定义在本文件作用域，见 s_retry_delay_ms） */
        s_retry_delay_ms = 500;
        if (network_task) xTaskNotifyGive(network_task);
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        connected = false;
        badge_dashboard_connection(false);
        wifi_event_sta_disconnected_t *event = (wifi_event_sta_disconnected_t *)data;
        ESP_LOGW(TAG, "Wi-Fi disconnected, reason %u", (unsigned)event->reason);

        /* 主动重连，但要退避。
         *
         * 2026-11-23 修复：原来无条件立即 esp_wifi_connect()。
         * 实测 reason=201（NO_AP_FOUND，热点暂时不可见）时，
         * 立即重连会形成高频重试风暴：扫描→失败→立刻再扫，
         * 射频一直处于忙态，反而更难重新发现 AP，同时挤占
         * WiFi 任务的 CPU 与功耗，拖累同一芯片上的 WebSocket 收发
         * （表现为心跳超时、playback_done 丢失、播放卡住）。
         *
         * 改为指数退避：500ms 起，每次翻倍，上限 8s。
         * 连上后（GOT_IP 事件）把计数清零，下次断线重新从 500ms 开始。
         * 退避用 vTaskDelay 在事件回调里是安全的：该回调运行在
         * WiFi 事件任务上下文，阻塞它不会阻塞协议栈内部收包。 */
        uint32_t delay_ms = s_retry_delay_ms;
        if (s_retry_delay_ms < 8000) {
            s_retry_delay_ms *= 2;
            if (s_retry_delay_ms > 8000) s_retry_delay_ms = 8000;
        }
        ESP_LOGI(TAG, "Wi-Fi 将在 %ums 后重连（下次退避 %ums）",
                 (unsigned)delay_ms, (unsigned)s_retry_delay_ms);
        vTaskDelay(pdMS_TO_TICKS(delay_ms));
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
    int64_t next_voice = 0;
    while (true) {
        int64_t now = esp_timer_get_time();
        if (!connected) {
            if (now >= next_connect) {
                esp_wifi_connect();
                next_connect = now + 10000000;
            }
            next_weather = 0;
            next_voice = 0;
        } else {
            /* 上电自动连接语音服务端（仅当已配置且尚未启动时）。
             * 失败则每 5 秒重试；用户手动发 VOICE 命令也能生效，
             * 因为 voice_is_started() 为真后这里就不再介入。 */
            if (strlen(BADGE_VOICE_AUTOCONNECT_HOST) && !voice_is_started() &&
                now >= next_voice) {
                esp_err_t r = voice_start(BADGE_VOICE_AUTOCONNECT_HOST,
                                          BADGE_VOICE_AUTOCONNECT_PORT);
                ESP_LOGI(TAG, "自动连接语音服务端 %s:%d -> %s",
                         BADGE_VOICE_AUTOCONNECT_HOST,
                         BADGE_VOICE_AUTOCONNECT_PORT,
                         esp_err_to_name(r));
                next_voice = now + (r == ESP_OK ? 0 : 5000000);
            }
            if (!sntp_started) {
                esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
                esp_sntp_setservername(0, "ntp.aliyun.com");
                esp_sntp_set_time_sync_notification_cb(on_time_sync);
                esp_sntp_init();
                sntp_started = true;
            }
            if (badge_dashboard_time_valid() && now >= next_weather) {
                bool success = fetch_weather();
                /* 失败计数驱动界面文案：0 次=正常，连续多次=明确提示"天气没拉到"，
                 * 而不是一直停在"更新中"让人以为没联网。 */
                weather_failures = success ? 0 : weather_failures + 1;
                badge_dashboard_weather_failed(weather_failures);
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
    sta_netif = esp_netif_create_default_wifi_sta();
    assert(sta_netif);
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
    /* 关闭 WiFi 省电（modem sleep）。
     * 默认 WIFI_PS_MIN_MODEM 在 Windows 移动热点（WiFi Direct）下会导致
     * 上行发送卡住：表现为 esp_transport_write() 超时、音频只上传几块就断，
     * 而手机热点正常。关掉省电后可显著改善吞吐与稳定性。 */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    assert(xTaskCreate(run_network, "dashboard_net", 8192, NULL, 2, &network_task) == pdPASS);
}
