/*
 * 语音客户端实现。
 *
 * 复用板级 BSP 的 esp_codec_dev 音频接口，不自己写 I2S/codec 寄存器。
 * 原因：BSP 的初始化序列来自官方，自己手写极易出错（已踩过一次坑）。
 */

#include "voice_client.h"

#include <stdio.h>
#include <string.h>

#include "badge_dashboard.h"
#include "bsp/esp-bsp.h"
#include "esp_codec_dev.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "voice";

/* ------------------------------------------------------------------ */
/* 协议常量：必须与 server/protocol/messages.py 一致                   */
/* ------------------------------------------------------------------ */

#define PROTO_VERSION     1
#define BIN_MAGIC         0xA5
#define BIN_HDR_SIZE      12
#define BIN_TYPE_UP       0x01
#define BIN_TYPE_DOWN     0x02

/* 录音格式：BSP 的 I2S 是 duplex mono 22050，这里保持一致 */
#define MIC_SAMPLE_RATE   22050
#define MIC_CHANNELS      2      /* BSP 配置为 stereo slot，实际 ES7210 双麦 */
#define MIC_BITS          16
#define CHUNK_MS          40
#define CHUNK_BYTES       (MIC_SAMPLE_RATE * MIC_CHANNELS * (MIC_BITS / 8) * CHUNK_MS / 1000)

/* 播放格式：TTS 输出 24000 Hz mono 16bit，服务端会按此下发 */
#define SPK_SAMPLE_RATE   24000
#define SPK_CHANNELS      1

/* 单轮音频上限 60 秒 */
#define MAX_TURN_SECONDS   60

/* ------------------------------------------------------------------ */

typedef enum {
    ST_OFF = 0,
    ST_CONNECTING,
    ST_IDLE,
    ST_RECORDING,
    ST_PLAYING,
} voice_state_t;

static esp_websocket_client_handle_t s_ws;
static SemaphoreHandle_t s_lock;
static volatile bool s_connected;
static volatile voice_state_t s_state = ST_OFF;

static void ui_sync(void);

/* 把内部状态映射成 UI 的 0..4，供 dashboard 显示 */
static void ui_sync(void)
{
    int v;
    switch (s_state) {
    case ST_CONNECTING: v = 1; break;
    case ST_IDLE:       v = 2; break;
    case ST_RECORDING:  v = 3; break;
    case ST_PLAYING:    v = 4; break;
    default:            v = 0; break;
    }
    badge_dashboard_voice(v);
}
static volatile bool s_talk_requested;
static volatile bool s_talk_stop_requested;

static char s_host[64];
static int  s_port;
static TaskHandle_t s_task;

/* 当前轮次 */
static char s_turn_id[48];
static uint32_t s_turn_short;
static uint16_t s_send_seq;

/* 播放统计，用于 playback_done 校验 */
static uint32_t s_recv_frames, s_expect_frames;
static uint32_t s_recv_bytes, s_expect_bytes;
static volatile bool s_playing;

/* 音频设备句柄 */
static esp_codec_dev_handle_t s_mic;
static esp_codec_dev_handle_t s_spk;

/* 录音缓冲（内部 RAM，避免 PSRAM 与 DMA 冲突） */
static int16_t *s_mic_buf;
static uint8_t *s_frame_buf;

/* ------------------------------------------------------------------ */
/* CRC32（与 Python zlib.crc32 一致）                                  */
/* ------------------------------------------------------------------ */

static uint32_t crc32_str(const char *s)
{
    uint32_t crc = 0xFFFFFFFFu;
    for (const unsigned char *p = (const unsigned char *)s; p && *p; ++p) {
        crc ^= (uint32_t)(*p);
        for (int i = 0; i < 8; ++i) {
            crc = (crc >> 1) ^ (0xEDB88320u & ((crc & 1u) ? 0xFFFFFFFFu : 0u));
        }
    }
    return crc ^ 0xFFFFFFFFu;
}

/* ------------------------------------------------------------------ */
/* 轻量 JSON 取值                                                      */
/* ------------------------------------------------------------------ */

static const char *json_value_start(const char *json, const char *key)
{
    const size_t klen = strlen(key);
    const char *p = json;
    while ((p = strchr(p, '"')) != NULL) {
        ++p;
        if (strncmp(p, key, klen) != 0 || p[klen] != '"') continue;
        const char *q = p + klen + 1;
        while (*q == ' ' || *q == '\t' || *q == '\n' || *q == '\r') ++q;
        if (*q != ':') continue;
        ++q;
        while (*q == ' ' || *q == '\t' || *q == '\n' || *q == '\r') ++q;
        return q;
    }
    return NULL;
}

static bool json_get_str(const char *json, const char *key, char *out, size_t cap)
{
    if (!out || cap == 0) return false;
    const char *v = json_value_start(json, key);
    if (!v || *v != '"') return false;
    ++v;
    size_t o = 0;
    while (*v && !(*v == '"' && v[-1] != '\\') && o + 1 < cap) {
        out[o++] = *v++;
    }
    out[o] = '\0';
    return true;
}

static bool json_get_int(const char *json, const char *key, int32_t *out)
{
    if (!out) return false;
    const char *v = json_value_start(json, key);
    if (!v) return false;
    char *end = NULL;
    const long val = strtol(v, &end, 10);
    if (end == v) return false;
    *out = (int32_t)val;
    return true;
}

/* ------------------------------------------------------------------ */
/* 发送                                                                */
/* ------------------------------------------------------------------ */

static esp_err_t send_text(const char *text)
{
    if (!s_connected || !s_ws || !text) return ESP_ERR_INVALID_STATE;
    const int ret = esp_websocket_client_send_text(s_ws, text, (int)strlen(text),
                                                   pdMS_TO_TICKS(2000));
    return ret >= 0 ? ESP_OK : ESP_FAIL;
}

/* 行缓冲：拼装 JSON，避免每处都写 snprintf */
#define JSON_BUF 320

/* ------------------------------------------------------------------ */
/* 音频：录音与播放                                                     */
/* ------------------------------------------------------------------ */

static esp_err_t audio_ensure(void)
{
    if (!s_mic) {
        s_mic = bsp_audio_codec_microphone_init();
        if (!s_mic) {
            ESP_LOGE(TAG, "麦克风初始化失败");
            return ESP_FAIL;
        }
    }
    if (!s_spk) {
        s_spk = bsp_audio_codec_speaker_init();
        if (!s_spk) {
            ESP_LOGE(TAG, "扬声器初始化失败");
            return ESP_FAIL;
        }
    }
    return ESP_OK;
}

/* 录音写入任务：从麦克风读块并上传 */
static void record_loop(void *arg)
{
    (void)arg;
    if (!s_mic_buf || !s_frame_buf) return;

    esp_codec_dev_sample_info_t fs = {
        .sample_rate = MIC_SAMPLE_RATE,
        .channel = MIC_CHANNELS,
        .bits_per_sample = MIC_BITS,
    };
    if (esp_codec_dev_open(s_mic, &fs) != ESP_OK) {
        ESP_LOGE(TAG, "麦克风打开失败");
        return;
    }

    uint32_t sent = 0;
    const uint32_t max_bytes = (uint32_t)MIC_SAMPLE_RATE * MIC_CHANNELS * 2 *
                               MAX_TURN_SECONDS;

    while (s_state == ST_RECORDING && s_connected && !s_talk_stop_requested) {
        if (esp_codec_dev_read(s_mic, s_mic_buf, CHUNK_BYTES) != ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

        /* 12 字节小端帧头 + PCM */
        s_frame_buf[0] = BIN_MAGIC;
        s_frame_buf[1] = BIN_TYPE_UP;
        s_frame_buf[2] = (uint8_t)(s_send_seq & 0xFF);
        s_frame_buf[3] = (uint8_t)((s_send_seq >> 8) & 0xFF);
        s_frame_buf[4] = (uint8_t)(s_turn_short & 0xFF);
        s_frame_buf[5] = (uint8_t)((s_turn_short >> 8) & 0xFF);
        s_frame_buf[6] = (uint8_t)((s_turn_short >> 16) & 0xFF);
        s_frame_buf[7] = (uint8_t)((s_turn_short >> 24) & 0xFF);
        s_frame_buf[8] = (uint8_t)(CHUNK_BYTES & 0xFF);
        s_frame_buf[9] = (uint8_t)((CHUNK_BYTES >> 8) & 0xFF);
        s_frame_buf[10] = 0;
        s_frame_buf[11] = 0;
        memcpy(s_frame_buf + BIN_HDR_SIZE, s_mic_buf, CHUNK_BYTES);

        const int n = esp_websocket_client_send_bin(
            s_ws, (const char *)s_frame_buf, BIN_HDR_SIZE + CHUNK_BYTES,
            pdMS_TO_TICKS(2000));
        if (n < 0) {
            ESP_LOGW(TAG, "上传失败，停止录音");
            break;
        }

        s_send_seq++;
        sent += CHUNK_BYTES;
        if (sent >= max_bytes) {
            ESP_LOGW(TAG, "达单轮上限 %d 秒", MAX_TURN_SECONDS);
            break;
        }
    }

    esp_codec_dev_close(s_mic);
    s_state = ST_IDLE; ui_sync();
}

/* ------------------------------------------------------------------ */
/* 下行消息处理                                                         */
/* ------------------------------------------------------------------ */

static void handle_playback_start(const char *json)
{
    int32_t frames = 0, bytes = 0;
    json_get_int(json, "frames", &frames);
    json_get_int(json, "bytes", &bytes);
    s_expect_frames = (uint32_t)frames;
    s_expect_bytes = (uint32_t)bytes;
    s_recv_frames = 0;
    s_recv_bytes = 0;
    s_playing = true;
    s_state = ST_PLAYING; ui_sync();

    if (audio_ensure() == ESP_OK) {
        esp_codec_dev_sample_info_t fs = {
            .sample_rate = SPK_SAMPLE_RATE,
            .channel = SPK_CHANNELS,
            .bits_per_sample = 16,
        };
        if (esp_codec_dev_open(s_spk, &fs) != ESP_OK) {
            ESP_LOGE(TAG, "扬声器打开失败");
        }
        esp_codec_dev_set_out_vol(s_spk, 60);
    }
    ESP_LOGI(TAG, "开始播放：预期 %u 帧 / %u 字节",
             (unsigned)frames, (unsigned)bytes);
}

static void finish_playback(void)
{
    if (!s_playing) return;
    s_playing = false;
    if (s_spk) esp_codec_dev_close(s_spk);

    char buf[JSON_BUF];
    snprintf(buf, sizeof(buf),
             "{\"type\":\"playback_done\",\"protocol_version\":%d,"
             "\"turn_id\":\"%s\",\"received_bytes\":%u,\"received_frames\":%u}",
             PROTO_VERSION, s_turn_id,
             (unsigned)s_recv_bytes, (unsigned)s_recv_frames);
    send_text(buf);

    ESP_LOGI(TAG, "播放完成：%u 帧 / %u 字节（预期 %u / %u）",
             (unsigned)s_recv_frames, (unsigned)s_recv_bytes,
             (unsigned)s_expect_frames, (unsigned)s_expect_bytes);
    s_state = ST_IDLE; ui_sync();
}

static void handle_binary(const uint8_t *data, size_t len)
{
    if (len < BIN_HDR_SIZE) {
        ESP_LOGW(TAG, "二进制帧过短: %u", (unsigned)len);
        return;
    }
    if (data[0] != BIN_MAGIC) {
        ESP_LOGW(TAG, "帧 magic 不符: 0x%02X", data[0]);
        return;
    }
    const uint8_t type = data[1];
    if (type != BIN_TYPE_DOWN) {
        return;   /* 只处理下行音频 */
    }
    const uint32_t plen = (uint32_t)data[8] | ((uint32_t)data[9] << 8) |
                          ((uint32_t)data[10] << 16) | ((uint32_t)data[11] << 24);
    if (plen > len - BIN_HDR_SIZE) {
        ESP_LOGW(TAG, "负载长度越界");
        return;
    }

    /* 轮次校验：旧轮帧必须丢弃 */
    const uint32_t tshort = (uint32_t)data[4] | ((uint32_t)data[5] << 8) |
                            ((uint32_t)data[6] << 16) | ((uint32_t)data[7] << 24);
    if (tshort != s_turn_short) {
        return;
    }

    s_recv_frames++;
    s_recv_bytes += plen;

    if (s_playing && s_spk) {
        if (esp_codec_dev_write(s_spk, (void *)(data + BIN_HDR_SIZE),
                                (int)plen) != ESP_OK) {
            ESP_LOGW(TAG, "写入音频失败");
        }
    }

    if (s_expect_frames && s_recv_frames >= s_expect_frames) {
        finish_playback();
    }
}

static void handle_text(const char *json)
{
    char type[32];
    if (!json_get_str(json, "type", type, sizeof(type))) return;

    if (strcmp(type, "server_hello") == 0) {
        ESP_LOGI(TAG, "握手完成");
    } else if (strcmp(type, "session_start") == 0) {
        json_get_str(json, "turn_id", s_turn_id, sizeof(s_turn_id));
        s_turn_short = crc32_str(s_turn_id);
        s_send_seq = 0;
        ESP_LOGI(TAG, "本轮 turn_id=%s", s_turn_id);

        /* 拿到 turn_id 之后才真正开始采集上传。
         * 顺序错会导致帧头 CRC 与轮次对不上，服务端直接丢弃音频。 */
        if (s_talk_requested) {
            s_talk_requested = false;
            s_talk_stop_requested = false;
            if (s_task) vTaskDelete(s_task);
            xTaskCreate(record_loop, "voice_rec", 4096, NULL, 5, &s_task);
        }
    } else if (strcmp(type, "asr_final") == 0) {
        char text[160];
        if (json_get_str(json, "text", text, sizeof(text))) {
            ESP_LOGI(TAG, "识别: %s", text);
        }
    } else if (strcmp(type, "llm_delta") == 0) {
        char text[160];
        if (json_get_str(json, "text", text, sizeof(text))) {
            printf("BADGE_VOICE {\"delta\":\"%s\"}\n", text);
        }
    } else if (strcmp(type, "llm_final") == 0) {
        char text[240];
        if (json_get_str(json, "text", text, sizeof(text))) {
            ESP_LOGI(TAG, "回复: %s", text);
        }
    } else if (strcmp(type, "playback_start") == 0) {
        handle_playback_start(json);
    } else if (strcmp(type, "turn_done") == 0) {
        finish_playback();
        s_state = ST_IDLE; ui_sync();
        ESP_LOGI(TAG, "本轮结束");
    } else if (strcmp(type, "turn_cancelled") == 0) {
        finish_playback();
        s_state = ST_IDLE; ui_sync();
    } else if (strcmp(type, "error") == 0) {
        char code[48], msg[128];
        json_get_str(json, "code", code, sizeof(code));
        json_get_str(json, "message", msg, sizeof(msg));
        ESP_LOGE(TAG, "服务端错误 %s: %s", code, msg);
        finish_playback();
        s_state = ST_IDLE; ui_sync();
    }
}

/* ------------------------------------------------------------------ */
/* WebSocket 事件                                                       */
/* ------------------------------------------------------------------ */

static void ws_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg; (void)base;
    esp_websocket_event_data_t *ev = (esp_websocket_event_data_t *)data;

    switch (id) {
    case WEBSOCKET_EVENT_CONNECTED:
        s_connected = true;
        s_state = ST_IDLE; ui_sync();
        ESP_LOGI(TAG, "已连接服务端");
        {
            char hello[JSON_BUF];
            snprintf(hello, sizeof(hello),
                     "{\"type\":\"device_hello\",\"protocol_version\":%d,"
                     "\"device_id\":\"ai-pet-badge\",\"firmware\":\"%s\","
                     "\"token\":\"\"}",
                     PROTO_VERSION, "1.0");
            send_text(hello);
        }
        break;

    case WEBSOCKET_EVENT_DISCONNECTED:
        s_connected = false;
        s_playing = false;
        s_state = ST_CONNECTING; ui_sync();
        ESP_LOGW(TAG, "连接断开");
        break;

    case WEBSOCKET_EVENT_DATA:
        if (!ev || ev->data_len <= 0) break;
        if (ev->op_code == 0x01) {
            /* 文本消息：本工程控制消息都很小，直接处理首片 */
            if (ev->payload_offset == 0) {
                static char s_json[512];
                const int n = ev->data_len < (int)sizeof(s_json) - 1
                                  ? ev->data_len : (int)sizeof(s_json) - 1;
                memcpy(s_json, ev->data_ptr, n);
                s_json[n] = '\0';
                handle_text(s_json);
            }
        } else if (ev->op_code == 0x02) {
            handle_binary((const uint8_t *)ev->data_ptr, (size_t)ev->data_len);
        }
        break;

    case WEBSOCKET_EVENT_ERROR:
        ESP_LOGW(TAG, "WebSocket 错误");
        break;

    default:
        break;
    }
}

/* ------------------------------------------------------------------ */
/* 对外接口                                                            */
/* ------------------------------------------------------------------ */

esp_err_t voice_start(const char *host, int port)
{
    if (s_ws) return ESP_OK;   /* 已启动 */

    if (!s_lock) {
        s_lock = xSemaphoreCreateMutex();
        if (!s_lock) return ESP_ERR_NO_MEM;
    }

    snprintf(s_host, sizeof(s_host), "%s", host ? host : "");
    s_port = port;

    if (!s_host[0]) {
        ESP_LOGE(TAG, "未指定服务端地址");
        return ESP_ERR_INVALID_ARG;
    }

    char uri[96];
    snprintf(uri, sizeof(uri), "ws://%s:%d/ws", s_host, s_port);

    esp_websocket_client_config_t cfg = {
        .uri = uri,
        .network_timeout_ms = 10000,
        .reconnect_timeout_ms = 5000,
        .buffer_size = 8192,
        .task_stack = 4096,
    };

    s_ws = esp_websocket_client_init(&cfg);
    if (!s_ws) {
        ESP_LOGE(TAG, "客户端创建失败");
        return ESP_FAIL;
    }
    esp_websocket_register_events(s_ws, WEBSOCKET_EVENT_ANY, ws_event, NULL);

    s_state = ST_CONNECTING; ui_sync();
    if (esp_websocket_client_start(s_ws) != ESP_OK) {
        ESP_LOGE(TAG, "连接启动失败");
        return ESP_FAIL;
    }

    /* 分配音频缓冲（内部 RAM，DMA 友好） */
    if (!s_mic_buf) {
        s_mic_buf = heap_caps_malloc(CHUNK_BYTES, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        s_frame_buf = heap_caps_malloc(BIN_HDR_SIZE + CHUNK_BYTES,
                                       MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (!s_mic_buf || !s_frame_buf) {
            ESP_LOGE(TAG, "音频缓冲分配失败");
            return ESP_ERR_NO_MEM;
        }
    }

    ESP_LOGI(TAG, "语音客户端已启动 -> %s", uri);
    return ESP_OK;
}

void voice_stop(void)
{
    if (!s_ws) return;
    s_connected = false;
    s_playing = false;
    s_state = ST_OFF; ui_sync();
    esp_websocket_client_stop(s_ws);
    esp_websocket_client_destroy(s_ws);
    s_ws = NULL;
    ESP_LOGI(TAG, "语音客户端已停止");
}

bool voice_connected(void)
{
    return s_connected;
}

esp_err_t voice_talk_start(void)
{
    if (!s_connected || s_state != ST_IDLE) {
        return ESP_ERR_INVALID_STATE;
    }
    if (audio_ensure() != ESP_OK) return ESP_FAIL;

    char req[JSON_BUF];
    snprintf(req, sizeof(req),
             "{\"type\":\"recording_start\",\"protocol_version\":%d,"
             "\"sample_rate\":%d,\"channels\":%d,\"bits\":%d,"
             "\"encoding\":\"pcm_s16le\",\"channel_layout\":\"RMNM\"}",
             PROTO_VERSION, MIC_SAMPLE_RATE, MIC_CHANNELS, MIC_BITS);
    if (send_text(req) != ESP_OK) {
        ESP_LOGE(TAG, "recording_start 发送失败");
        return ESP_FAIL;
    }

    s_talk_stop_requested = false;
    s_talk_requested = true;

    /* 录音任务不在这里启动，等 session_start 拿到 turn_id 后再起。
     * 这里只负责发出请求。 */
    ESP_LOGI(TAG, "已请求开始录音，等待 session_start");
    return ESP_OK;
}

esp_err_t voice_talk_stop(void)
{
    if (s_talk_requested || s_state == ST_RECORDING) {
        s_talk_stop_requested = true;
    }
    s_talk_requested = false;
    s_state = ST_IDLE; ui_sync();

    char buf[JSON_BUF];
    snprintf(buf, sizeof(buf),
             "{\"type\":\"recording_stop\",\"protocol_version\":%d}", PROTO_VERSION);
    send_text(buf);
    ESP_LOGI(TAG, "停止录音");
    return ESP_OK;
}

void voice_print_status(void)
{
    static const char *names[] = {"OFF", "CONNECTING", "IDLE", "RECORDING", "PLAYING"};
    const int st = (int)s_state;
    printf("BADGE_VOICE {\"state\":\"%s\",\"connected\":%s,\"turn\":\"%s\","
           "\"recv_frames\":%u,\"recv_bytes\":%u}\n",
           names[(st >= 0 && st < 5) ? st : 0],
           s_connected ? "true" : "false",
           s_turn_id, (unsigned)s_recv_frames, (unsigned)s_recv_bytes);
}
