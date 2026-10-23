#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "app_config.h"
#include "app_json.h"
#include "app_limits.h"
#include "app_net.h"
#include "app_protocol.h"
#include "app_text.h"
#include "app_ui.h"

#include "driver/usb_serial_jtag.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "main";

/* 串口读入的一行输入。 */
static char s_line[APP_MAX_LINE_BYTES + 1];
static char s_request[APP_MAX_REQUEST_BYTES + 1];
static char s_session_id[APP_SESSION_ID_CHARS + 1];
static int  s_request_seq;

/* 生成每轮唯一的请求标识，避免网关去重把新消息当作重复处理。 */
static void next_request_id(char *out, size_t out_size)
{
    ++s_request_seq;
    snprintf(out, out_size, "esp32-%d", s_request_seq);
}

/* 从 USB 串口读一行，返回长度；超长行被拒绝并返回 -1。 */
static int read_line(char *buf, size_t cap)
{
    size_t used = 0;
    while (true) {
        uint8_t ch = 0;
        const int n = (int)usb_serial_jtag_read_bytes(&ch, 1, pdMS_TO_TICKS(50));
        if (n <= 0) {
            continue;  /* 没数据就继续等，由上层决定何时认为空闲 */
        }
        if (ch == '\r') {
            continue;
        }
        if (ch == '\n') {
            buf[used] = '\0';
            return (int)used;
        }
        if (used + 1 >= cap) {
            /* 行过长：丢弃到行尾，避免把半句话发出去 */
            while (true) {
                const int m = (int)usb_serial_jtag_read_bytes(&ch, 1, pdMS_TO_TICKS(50));
                if (m <= 0 || ch == '\n') {
                    break;
                }
            }
            buf[0] = '\0';
            return -1;
        }
        buf[used++] = (char)ch;
    }
}

static void handle_error(app_error_t error)
{
    ESP_LOGW(TAG, "错误：%s", app_error_text(error));
    app_ui_set_state(APP_STATE_ERROR);
    app_ui_show_error(error);
}

static void run_once(const char *message)
{
    if (!app_utf8_fits(message, APP_MAX_MESSAGE_BYTES)) {
        handle_error(APP_ERROR_LOCAL);
        return;
    }

    char request_id[32];
    next_request_id(request_id, sizeof(request_id));

    const size_t len = app_build_chat_request(
        request_id, s_session_id[0] ? s_session_id : NULL,
        message, s_request, sizeof(s_request));
    if (len == 0) {
        handle_error(APP_ERROR_LOCAL);
        return;
    }

    app_ui_set_state(APP_STATE_SENT);
    app_ui_show_user(message);

    app_reply_t reply;
    if (!app_net_chat(APP_GATEWAY_BASE_URL, APP_GATEWAY_DEVICE_TOKEN,
                      s_request, &reply)) {
        /* 会话过期时清掉本地标识，下一轮自动新建 */
        if (reply.error == APP_ERROR_SESSION_NOT_FOUND) {
            s_session_id[0] = '\0';
        }
        handle_error(reply.error);
        return;
    }

    strncpy(s_session_id, reply.session_id, sizeof(s_session_id) - 1);
    s_session_id[sizeof(s_session_id) - 1] = '\0';
    /* 回复最长可达 APP_MAX_ANSWER_CHARS，不能塞进按输入行尺寸分配的 s_line */
    ESP_LOGI(TAG, "回答：%s", reply.reply);

    app_ui_set_state(APP_STATE_ANSWER);
    app_ui_show_answer(reply.reply);
}

void app_main(void)
{
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    usb_serial_jtag_driver_install(&cfg);

    ESP_LOGI(TAG, "zjy11 文字版固件启动");
    ESP_LOGI(TAG, "芯片：ESP32-S3，Flash 16MB，PSRAM 8MB");

    app_ui_set_state(APP_STATE_BOOT);
    app_ui_init();

    app_ui_set_state(APP_STATE_CONNECTING);
    /*
     * Wi-Fi 未配置或失败时**不退出**：
     * 第一版是调试阶段的设备，串口必须始终可用，
     * 否则用户看不到任何诊断信息。
     */
    bool wifi_ok = app_net_init(APP_WIFI_SSID, APP_WIFI_PASSWORD);
    if (!wifi_ok) {
        ESP_LOGE(TAG, "Wi-Fi 未连接：请在 idf.py menuconfig 中配置 SSID 与密码");
        handle_error(APP_ERROR_NETWORK);
    } else {
        bool gateway_ready = false;
        if (!app_net_health(APP_GATEWAY_BASE_URL, APP_GATEWAY_DEVICE_TOKEN,
                            &gateway_ready)) {
            ESP_LOGE(TAG, "无法访问网关：请检查 IP 是否可达");
            handle_error(APP_ERROR_NETWORK);
        } else if (!gateway_ready) {
            /* 网关在跑但云端未配置：不伪造回答，明确告知 */
            ESP_LOGW(TAG, "网关可达，但云端模型尚未配置");
            handle_error(APP_ERROR_CLOUD_NOT_CONFIGURED);
        } else {
            ESP_LOGI(TAG, "网关就绪，可以开始输入文字");
            app_ui_set_state(APP_STATE_READY);
        }
    }

    while (true) {
        app_ui_tick();

        const int n = read_line(s_line, sizeof(s_line));
        if (n == -1) {
            handle_error(APP_ERROR_LOCAL);
            continue;
        }
        if (n <= 0) {
            continue;
        }

        if (strcmp(s_line, "/reset") == 0) {
            if (app_net_ready()) {
                app_net_reset_session(APP_GATEWAY_BASE_URL, APP_GATEWAY_DEVICE_TOKEN,
                                      s_session_id);
            }
            s_session_id[0] = '\0';
            app_ui_set_state(APP_STATE_READY);
            continue;
        }
        if (strcmp(s_line, "/settings") == 0) {
            /*
             * Wi-Fi 未就绪时不能调用任何网络接口：
             * tcpip 协议栈未初始化会触发 "Invalid mbox" 断言并崩溃。
             */
            bool ready = false;
            if (app_net_ready()) {
                app_net_health(APP_GATEWAY_BASE_URL, APP_GATEWAY_DEVICE_TOKEN, &ready);
            }
            app_ui_set_state(APP_STATE_SETTINGS);
            app_ui_show_settings(APP_WIFI_SSID, APP_GATEWAY_BASE_URL, ready,
                                 app_net_ready() ? "friend-v1" : "未联网");
            continue;
        }

        if (!app_net_ready()) {
            ESP_LOGW(TAG, "网络未就绪，无法发送：%s", s_line);
            handle_error(APP_ERROR_NETWORK);
            continue;
        }

        run_once(s_line);
    }
}
