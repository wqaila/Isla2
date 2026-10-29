#include "sdkconfig.h"

/*
 * ESP32-S3 智能角色语音交互系统 —— 固件主程序
 *
 * 职责边界（很重要）：
 *   板子**不做任何推理**。ASR / LLM / TTS 全部在电脑侧完成。
 *   固件只负责：录音上传 / 收音频播放 / 显示文字 / 处理按键。
 *
 * 一次对话的数据流：
 *
 *   按下触摸 → recording_start → 持续上传 audio_chunk(二进制)
 *   → recording_stop → [服务端 ASR+LLM+TTS]
 *   → asr_final（显示识别文本）
 *   → llm_delta × N（流式显示回复）
 *   → playback_start（含总字节数/帧数）
 *   → tts_audio × N（二进制）
 *   → playback_done（上报实收字节数，服务端校验）
 *   → turn_done → 回到 IDLE
 */

#include <stdio.h>
#include <string.h>

#include "app_audio.h"
#include "app_board.h"
#include "app_config.h"
#include "app_net.h"
#include "app_protocol.h"
#include "app_state.h"
#include "app_ui.h"

#include "driver/uart.h"
#include "driver/usb_serial_jtag.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs_flash.h"

static const char *TAG = "main";

/* 等待服务端 session_start 的信号位 */
static EventGroupHandle_t s_turn_events;
#define TURN_SESSION_BIT BIT0

/* ===================================================================
 * 运行时上下文
 * =================================================================== */

typedef struct {
    char     session_id[48];
    char     turn_id[48];      /* 当前轮次，来自 session_start */
    uint32_t turn_short;       /* turn_id 的 CRC32，用于二进制帧头 */

    uint16_t send_seq;         /* 上行块序号 */

    /* 下行音频统计，用于 playback_done 校验 */
    uint32_t recv_bytes;
    uint32_t recv_frames;
    uint32_t expect_bytes;
    uint32_t expect_frames;
    bool     playback_active;

    int      out_sample_rate;  /* 播放采样率，来自 server_hello */

    /* 心跳 */
    uint32_t last_msg_ms;
    uint32_t last_heartbeat_ms;
} runtime_t;

static runtime_t s_rt;

/* 录音任务句柄 */
static TaskHandle_t s_record_task;

/* ===================================================================
 * 上行：录音并上传
 * =================================================================== */

/* 完整的录音请求缓冲；由 record_task 使用，避免在栈上开 5KB */
typedef struct {
    uint8_t *pcm;
    uint8_t *frame;
} record_bufs_t;

static record_bufs_t s_rec_bufs;

static void record_task(void *arg)
{
    (void)arg;

    /* 40ms @ 16kHz 4ch 16bit = 5120 字节 */
    const size_t chunk_bytes =
        (size_t)(APP_MIC_SAMPLE_RATE * APP_MIC_CHANNELS * 2) *
        APP_AUDIO_CHUNK_MS / 1000;

    uint8_t *pcm = s_rec_bufs.pcm;
    uint8_t *frame = s_rec_bufs.frame;
    if (pcm == NULL || frame == NULL) {
        ESP_LOGE(TAG, "录音缓冲未就绪");
        goto cleanup;
    }

    /* 关键：先告知服务端开始录音并等它分配 turn。
     * 原实现直接采集上传，服务端没有本轮上下文，turn_id 为空、
     * CRC 为 0，音频会被判为无效或被丢弃。 */
    {
        char req[192];
        int n = app_proto_build_recording_start(
            req, sizeof(req), APP_MIC_SAMPLE_RATE, APP_MIC_CHANNELS, 16,
            "pcm_s16le", APP_MIC_LAYOUT);
        if (n <= 0) {
            ESP_LOGE(TAG, "构造 recording_start 失败");
            goto cleanup;
        }
        xEventGroupClearBits(s_turn_events, TURN_SESSION_BIT);
        if (app_ws_send_text(req, (size_t)n) != ESP_OK) {
            ESP_LOGE(TAG, "发送 recording_start 失败");
            goto cleanup;
        }
    }

    /* 等 session_start：服务端返回 turn_id 后才允许上传音频 */
    const EventBits_t bits = xEventGroupWaitBits(
        s_turn_events, TURN_SESSION_BIT, pdTRUE, pdFALSE,
        pdMS_TO_TICKS(5000));
    if ((bits & TURN_SESSION_BIT) == 0) {
        ESP_LOGE(TAG, "等待 session_start 超时，取消本轮录音");
        goto cleanup;
    }
    if (s_rt.turn_id[0] == '\0') {
        ESP_LOGE(TAG, "session_start 未返回 turn_id");
        goto cleanup;
    }
    ESP_LOGI(TAG, "本轮 turn_id=%s，开始采集", s_rt.turn_id);

    /* 采集必须在拿到 turn_id 之后才开始，否则帧头 CRC 对不上 */
    if (app_audio_record_start() != ESP_OK) {
        ESP_LOGE(TAG, "采集启动失败");
        goto cleanup;
    }

    s_rt.send_seq = 0;
    ESP_LOGI(TAG, "录音开始，块 %u 字节 / %d ms", (unsigned)chunk_bytes,
             APP_AUDIO_CHUNK_MS);

    uint32_t sent_bytes = 0;
    const uint32_t max_bytes =
        (uint32_t)APP_MIC_SAMPLE_RATE * APP_MIC_CHANNELS * 2 *
        APP_MAX_TURN_AUDIO_SEC;

    while (app_audio_recording() && app_ws_connected() &&
           app_state_get() == APP_STATE_RECORDING) {
        size_t got = 0;
        esp_err_t err = app_audio_record_read(pcm, chunk_bytes, &got, 200);
        if (err != ESP_OK || got == 0) {
            continue;
        }

        /* 只上传整帧数据：I2S 可能返回非整帧长度，
         * 若直接上传会让服务端按 4ch 解交错时错位。 */
        const size_t frame_bytes = APP_MIC_CHANNELS * 2;
        const size_t usable = got - (got % frame_bytes);
        if (usable == 0) {
            continue;
        }

        size_t total = 0;
        err = app_proto_encode_audio(s_rt.send_seq, s_rt.turn_short,
                                     pcm, usable, frame,
                                     APP_BINARY_HEADER_SIZE + chunk_bytes,
                                     &total);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "编码失败: %s", esp_err_to_name(err));
            continue;
        }

        if (app_ws_send_bin(frame, total) != ESP_OK) {
            ESP_LOGW(TAG, "上传失败，停止录音");
            break;
        }

        s_rt.send_seq++;
        sent_bytes += usable;

        if (sent_bytes >= max_bytes) {
            ESP_LOGW(TAG, "达单轮上限 %d 秒，自动停止",
                     APP_MAX_TURN_AUDIO_SEC);
            break;
        }
    }

cleanup:
    /* 无论正常结束还是出错，都要停止采集并通知服务端收尾 */
    if (app_audio_recording()) {
        app_audio_record_stop();
    }

    {
        char buf[128];
        if (app_proto_build_recording_stop(buf, sizeof(buf)) >= 0) {
            app_ws_send_text(buf, strlen(buf));
        }
    }

    ESP_LOGI(TAG, "录音结束，共上传 %u 块", (unsigned)s_rt.send_seq);

    if (app_state_get() == APP_STATE_RECORDING) {
        app_state_try_enter(APP_STATE_ASR_PROCESSING);
    }
    app_ui_set_hint("识别中...");

    s_record_task = NULL;
    vTaskDelete(NULL);
}

static void start_recording(void)
{
    if (app_state_busy()) {
        ESP_LOGW(TAG, "忙碌中（%s），忽略开始录音",
                 app_state_name(app_state_get()));
        app_ui_set_hint("正忙，稍等");
        return;
    }
    if (!app_ws_connected()) {
        ESP_LOGW(TAG, "未连接服务端，无法录音");
        app_ui_set_hint("未连接");
        return;
    }

    if (!app_state_try_enter(APP_STATE_RECORDING)) {
        return;
    }

    app_ui_clear_reply();
    app_ui_set_asr("");
    app_ui_set_hint("录音中，松手结束");

    s_rt.turn_id[0] = '\0';
    s_rt.turn_short = 0;

    xTaskCreate(record_task, "record", 4096, NULL, 5, &s_record_task);
}

static void stop_recording(void)
{
    if (app_state_get() != APP_STATE_RECORDING) {
        return;
    }
    /* record_task 会看到 s_recording 变 false 后退出并自行收尾 */
    app_audio_record_stop();
}

/* ===================================================================
 * 下行：处理服务端消息
 * =================================================================== */

static void handle_server_hello(const char *json)
{
    char server[32];
    if (app_json_get_string(json, "server_name", server, sizeof(server))) {
        ESP_LOGI(TAG, "服务端: %s", server);
    }

    /* audio_out.sample_rate 是嵌套对象里的字段，不能取顶层 sample_rate
     * （顶层若有 audio_in.sample_rate=16000，会被误当成播放采样率）。 */
    char out_obj[160];
    if (app_json_get_string_raw_object(json, "audio_out", out_obj,
                                       sizeof(out_obj))) {
        int32_t sr = 0, ch = 0, bits = 0;
        if (app_json_get_int(out_obj, "sample_rate", &sr) && sr > 0) {
            s_rt.out_sample_rate = sr;
        }
        if (app_json_get_int(out_obj, "channels", &ch) && ch != 1) {
            ESP_LOGW(TAG, "服务端下行通道数为 %d，本固件按单声道播放", (int)ch);
        }
        if (app_json_get_int(out_obj, "bits", &bits) && bits != 16) {
            ESP_LOGW(TAG, "服务端下行位深为 %d，本固件按 16bit 播放", (int)bits);
        }
    } else {
        ESP_LOGW(TAG, "server_hello 缺少 audio_out，按默认 24000 处理");
    }

    ESP_LOGI(TAG, "握手完成，播放采样率 %d", s_rt.out_sample_rate);
    app_ui_set_status("已连接");
}

static void handle_session_start(const char *json)
{
    app_json_get_string(json, "session_id", s_rt.session_id,
                         sizeof(s_rt.session_id));
    if (app_json_get_string(json, "turn_id", s_rt.turn_id,
                            sizeof(s_rt.turn_id))) {
        s_rt.turn_short = app_proto_turn_short(s_rt.turn_id);
        ESP_LOGI(TAG, "本轮 turn_id=%s (short=%08X)", s_rt.turn_id,
                 (unsigned)s_rt.turn_short);
    } else {
        ESP_LOGW(TAG, "session_start 缺少 turn_id");
    }
    s_rt.recv_bytes = 0;
    s_rt.recv_frames = 0;
    s_rt.expect_bytes = 0;
    s_rt.expect_frames = 0;

    /* 唤醒正在等 turn_id 的录音任务 */
    if (s_turn_events) {
        xEventGroupSetBits(s_turn_events, TURN_SESSION_BIT);
    }
}

static void handle_asr_final(const char *json)
{
    char text[256];
    if (app_json_get_string(json, "text", text, sizeof(text))) {
        ESP_LOGI(TAG, "识别: %s", text);
        app_ui_set_asr(text);
    }
    app_state_try_enter(APP_STATE_LLM_PROCESSING);
    app_ui_set_hint("思考中...");
}

static void handle_llm_delta(const char *json)
{
    char text[256];
    if (app_json_get_string(json, "text", text, sizeof(text))) {
        app_ui_append_reply(text);
    }
    if (app_state_get() == APP_STATE_ASR_PROCESSING) {
        app_state_try_enter(APP_STATE_LLM_PROCESSING);
    }
}

static void handle_llm_final(const char *json)
{
    char text[512];
    if (app_json_get_string(json, "text", text, sizeof(text))) {
        ESP_LOGI(TAG, "回复: %s", text);
    }
    app_state_try_enter(APP_STATE_TTS_PROCESSING);
    app_ui_set_hint("合成语音...");
}

static void handle_playback_start(const char *json)
{
    int32_t bytes = 0, frames = 0, sr = 0;
    if (app_json_get_int(json, "bytes", &bytes)) {
        s_rt.expect_bytes = (uint32_t)bytes;
    }
    if (app_json_get_int(json, "frames", &frames)) {
        s_rt.expect_frames = (uint32_t)frames;
    }
    if (app_json_get_int(json, "sample_rate", &sr)) {
        s_rt.out_sample_rate = sr;
    }
    s_rt.recv_bytes = 0;
    s_rt.recv_frames = 0;
    s_rt.playback_active = true;

    ESP_LOGI(TAG, "开始接收音频：预期 %u 字节 / %u 帧 @ %d Hz",
             (unsigned)s_rt.expect_bytes, (unsigned)s_rt.expect_frames,
             s_rt.out_sample_rate);

    if (!app_state_try_enter(APP_STATE_PLAYING)) {
        ESP_LOGW(TAG, "状态机拒绝进入 PLAYING");
    }
    app_ui_set_hint("播放中...");
    app_audio_play_start();
}

static void finish_playback(void)
{
    if (!s_rt.playback_active) {
        return;
    }
    s_rt.playback_active = false;

    /* 必须等 DMA 真正播完再上报，否则服务端会在音频还没放完时开下一轮 */
    app_audio_play_drain(3000);
    app_audio_play_stop();

    char buf[256];
    if (app_proto_build_playback_done(buf, sizeof(buf), s_rt.turn_id,
                                      s_rt.recv_bytes, s_rt.recv_frames) >= 0) {
        app_ws_send_text(buf, strlen(buf));
    }

    ESP_LOGI(TAG, "播放完成：收到 %u 帧 / %u 字节（预期 %u / %u）%s",
             (unsigned)s_rt.recv_frames, (unsigned)s_rt.recv_bytes,
             (unsigned)s_rt.expect_frames, (unsigned)s_rt.expect_bytes,
             (s_rt.recv_bytes == s_rt.expect_bytes) ? "" : "  ⚠ 不一致");

    if (s_rt.expect_bytes && s_rt.recv_bytes != s_rt.expect_bytes) {
        app_ui_show_error("audio_mismatch", "音频字节数不一致");
    }
}

static void handle_turn_done(const char *json)
{
    char reason[48];
    if (app_json_get_string(json, "reason", reason, sizeof(reason))) {
        ESP_LOGI(TAG, "本轮结束: %s", reason);
    }
    finish_playback();
    app_state_reset();
    app_ui_set_hint("按屏幕开始对话");
}

static void handle_turn_cancelled(const char *json)
{
    char reason[48];
    if (app_json_get_string(json, "reason", reason, sizeof(reason))) {
        ESP_LOGI(TAG, "本轮被取消: %s", reason);
    }
    finish_playback();
    app_state_reset();
    app_ui_set_hint("已取消");
}

static void handle_error(const char *json)
{
    char code[48], msg[128];
    bool fatal = false;
    app_json_get_string(json, "code", code, sizeof(code));
    app_json_get_string(json, "message", msg, sizeof(msg));
    app_json_get_bool(json, "fatal", &fatal);

    ESP_LOGE(TAG, "服务端错误 %s: %s%s", code, msg, fatal ? "（不可恢复）" : "");

    finish_playback();
    app_state_try_enter(APP_STATE_ERROR);
    app_ui_show_error(code, msg);

    if (fatal) {
        /* 协议不兼容等致命错误：断开重连让握手重来 */
        ESP_LOGW(TAG, "致命错误，断开重连");
        app_ws_restart();
    } else {
        vTaskDelay(pdMS_TO_TICKS(1500));
        app_state_reset();
        app_ui_set_hint("按屏幕开始对话");
    }
}

static void handle_text_message(const char *text, size_t len)
{
    /* 保留一份可修改副本：解析函数需要 NUL 结尾 */
    static char s_json[APP_MAX_JSON_BYTES];
    if (len >= sizeof(s_json)) {
        ESP_LOGW(TAG, "消息过大 %u 字节，丢弃", (unsigned)len);
        return;
    }
    memcpy(s_json, text, len);
    s_json[len] = '\0';

    s_rt.last_msg_ms = xTaskGetTickCount() * portTICK_PERIOD_MS;

    char type[32];
    if (!app_json_get_string(s_json, "type", type, sizeof(type))) {
        ESP_LOGW(TAG, "消息缺少 type 字段");
        return;
    }

    /* 协议版本校验：不匹配说明固件与服务端版本不一致，必须报出来 */
    int32_t ver = 0;
    if (app_json_get_int(s_json, "protocol_version", &ver) && ver != APP_PROTOCOL_VERSION) {
        ESP_LOGE(TAG, "协议版本不匹配：设备 %d / 服务端 %d",
                 APP_PROTOCOL_VERSION, (int)ver);
        app_ui_show_error("protocol_version_mismatch", "服务端协议版本不同");
        return;
    }

    if (strcmp(type, APP_MSG_SERVER_HELLO) == 0) {
        handle_server_hello(s_json);
    } else if (strcmp(type, APP_MSG_SESSION_START) == 0) {
        handle_session_start(s_json);
    } else if (strcmp(type, APP_MSG_ASR_FINAL) == 0) {
        handle_asr_final(s_json);
    } else if (strcmp(type, APP_MSG_LLM_START) == 0) {
        app_state_try_enter(APP_STATE_LLM_PROCESSING);
    } else if (strcmp(type, APP_MSG_LLM_DELTA) == 0) {
        handle_llm_delta(s_json);
    } else if (strcmp(type, APP_MSG_LLM_FINAL) == 0) {
        handle_llm_final(s_json);
    } else if (strcmp(type, APP_MSG_TTS_START) == 0) {
        app_state_try_enter(APP_STATE_TTS_PROCESSING);
    } else if (strcmp(type, APP_MSG_PLAYBACK_START) == 0) {
        handle_playback_start(s_json);
    } else if (strcmp(type, APP_MSG_TURN_DONE) == 0) {
        handle_turn_done(s_json);
    } else if (strcmp(type, APP_MSG_TURN_CANCELLED) == 0) {
        handle_turn_cancelled(s_json);
    } else if (strcmp(type, APP_MSG_ERROR) == 0) {
        handle_error(s_json);
    } else if (strcmp(type, APP_MSG_HEARTBEAT_ACK) == 0) {
        /* 心跳应答，last_msg_ms 已更新，无需额外处理 */
    } else {
        ESP_LOGW(TAG, "未知消息类型: %s", type);
    }
}

static void handle_binary_message(const uint8_t *data, size_t len)
{
    app_bin_header_t hdr;
    esp_err_t err = app_proto_decode_audio(data, len, &hdr);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "二进制帧解析失败: %s", esp_err_to_name(err));
        return;
    }
    if (hdr.msg_type != APP_BIN_TYPE_AUDIO_DOWN) {
        return;   /* 上行帧不该被回传，忽略 */
    }

    /* 轮次校验：旧轮次的迟到帧必须丢弃。
     * 原实现直接播放，会把上一轮残留音频播到新一轮里。 */
    if (hdr.turn_short != s_rt.turn_short) {
        ESP_LOGD(TAG, "丢弃非本轮帧 turn=%08X 当前=%08X",
                 (unsigned)hdr.turn_short, (unsigned)s_rt.turn_short);
        return;
    }

    /* 序号校验：重复或明显超前的帧直接丢弃，避免重复播放同一段 */
    if (s_rt.recv_frames > 0 && hdr.seq + 1u < (uint16_t)s_rt.recv_frames) {
        ESP_LOGW(TAG, "丢弃重复/乱序帧 seq=%u 已收=%u",
                 (unsigned)hdr.seq, (unsigned)s_rt.recv_frames);
        return;
    }

    const uint8_t *pcm = data + APP_BINARY_HEADER_SIZE;
    const size_t pcm_len = hdr.payload_len;

    s_rt.recv_bytes += (uint32_t)pcm_len;
    s_rt.recv_frames++;

    /* 流式播放：收到就写，不攒整段（整段 2.8MB 装不下） */
    if (s_rt.playback_active) {
        size_t written = 0;
        esp_err_t werr = app_audio_play_write(pcm, pcm_len, &written, 2000);
        if (werr != ESP_OK) {
            ESP_LOGW(TAG, "写入播放缓冲失败: %s", esp_err_to_name(werr));
        } else if (written != pcm_len) {
            /* 部分写入会导致播放缺帧，必须记录，否则表现为随机断音 */
            ESP_LOGW(TAG, "播放缓冲只写入 %u/%u 字节",
                     (unsigned)written, (unsigned)pcm_len);
        }
    }

    /* 收齐了就主动上报，不必等服务端催 */
    if (s_rt.expect_frames && s_rt.recv_frames >= s_rt.expect_frames) {
        finish_playback();
    }
}

static void on_ws_text(const char *text, size_t len, void *user)
{
    (void)user;
    handle_text_message(text, len);
}

static void on_ws_bin(const uint8_t *data, size_t len, void *user)
{
    (void)user;
    handle_binary_message(data, len);
}

static void on_ws_state(bool connected, void *user)
{
    (void)user;
    if (connected) {
        char buf[192];
        const esp_app_desc_t *desc = esp_app_get_description();
        int n = app_proto_build_hello(buf, sizeof(buf), APP_DEVICE_ID,
                                      desc ? desc->version : "dev",
                                      APP_DEVICE_TOKEN);
        if (n >= 0) {
            app_ws_send_text(buf, (size_t)n);
            ESP_LOGI(TAG, "已发送 device_hello");
        }
        app_ui_set_status("已连接");
        app_ui_set_hint("按屏幕开始对话");
        app_state_try_enter(APP_STATE_IDLE);
    } else {
        app_ui_set_status("连接中...");
        /* 断线时若正在录音/播放，必须清理，否则状态卡死 */
        if (app_audio_recording()) {
            app_audio_record_stop();
        }
        if (app_audio_playing()) {
            app_audio_play_stop();
        }
        s_rt.playback_active = false;
        app_state_reset();
    }
}

/* ===================================================================
 * 心跳任务
 * =================================================================== */

static void heartbeat_task(void *arg)
{
    (void)arg;
    char buf[96];
    uint32_t now = 0;

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        if (!app_ws_connected()) {
            continue;
        }

        now = xTaskGetTickCount() * portTICK_PERIOD_MS;

        /* 主动发心跳 */
        if (now - s_rt.last_heartbeat_ms >= APP_HEARTBEAT_INTERVAL_MS) {
            s_rt.last_heartbeat_ms = now;
            if (app_proto_build_heartbeat(buf, sizeof(buf)) >= 0) {
                app_ws_send_text(buf, strlen(buf));
            }
        }

        /* 超时检测 */
        if (s_rt.last_msg_ms &&
            now - s_rt.last_msg_ms > APP_HEARTBEAT_TIMEOUT_MS) {
            ESP_LOGW(TAG, "心跳超时（%u ms 无消息），断开重连",
                     (unsigned)(now - s_rt.last_msg_ms));
            s_rt.last_msg_ms = now;
            app_ws_restart();
        }
    }
}

/* ===================================================================
 * 入口
 * =================================================================== */

void app_main(void)
{
    const esp_app_desc_t *desc = esp_app_get_description();
    ESP_LOGI(TAG, "=== zjy13 语音固件 %s ===", desc ? desc->version : "dev");
    ESP_LOGI(TAG, "目标服务端 ws://%s:%d", APP_SERVER_HOST, APP_SERVER_PORT);

    memset(&s_rt, 0, sizeof(s_rt));
    s_rt.out_sample_rate = APP_SPK_SAMPLE_RATE;
    s_rt.last_msg_ms = xTaskGetTickCount() * portTICK_PERIOD_MS;

    /* 1) NVS 必须先初始化。
     * ESP-IDF 的 PHY 在读射频校准数据时明确要求调用方先 nvs_flash_init()，
     * 否则 Wi-Fi/BLE 启动会报 "NVS has not been initialized"。 */
    {
        esp_err_t nvs = nvs_flash_init();
        if (nvs == ESP_ERR_NVS_NO_FREE_PAGES ||
            nvs == ESP_ERR_NVS_NEW_VERSION_FOUND) {
            /* 分区被写满或版本不匹配时，只能擦掉 NVS 重建。
             * 注意：这会清掉原固件留在 NVS 里的数据（含 Wi-Fi 凭据），
             * 属破坏性操作，因此只在必要时执行。 */
            ESP_LOGW(TAG, "NVS 需要重建（%s）", esp_err_to_name(nvs));
            ESP_ERROR_CHECK(nvs_flash_erase());
            nvs = nvs_flash_init();
        }
        if (nvs != ESP_OK) {
            ESP_LOGE(TAG, "NVS 初始化失败: %s", esp_err_to_name(nvs));
        }
    }

    ESP_LOGI(TAG, "空闲内存: 内部 %u KB / PSRAM %u KB",
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024));

    app_state_init();
    app_ui_init();

    /* 2) 预分配录音缓冲。
     * 原来在 record_task 里 malloc/free，任务异常退出会泄漏；
     * 改为启动时分配一次，全程复用。 */
    {
        const size_t chunk_bytes =
            (size_t)(APP_MIC_SAMPLE_RATE * APP_MIC_CHANNELS * 2) *
            APP_AUDIO_CHUNK_MS / 1000;
        s_rec_bufs.pcm = heap_caps_malloc(chunk_bytes, MALLOC_CAP_SPIRAM);
        s_rec_bufs.frame = heap_caps_malloc(
            APP_BINARY_HEADER_SIZE + chunk_bytes, MALLOC_CAP_SPIRAM);
        if (s_rec_bufs.pcm == NULL || s_rec_bufs.frame == NULL) {
            ESP_LOGE(TAG, "录音缓冲分配失败（PSRAM 不足？）");
            app_ui_show_error("no_mem", "录音缓冲分配失败");
        }
    }

    s_turn_events = xEventGroupCreate();
    if (s_turn_events == NULL) {
        ESP_LOGE(TAG, "事件组创建失败");
        app_ui_show_error("no_mem", "事件组创建失败");
    }

    /* 3) Wi-Fi */
    const char *ssid = APP_WIFI_SSID;
    const char *pass = APP_WIFI_PASSWORD;
    if (ssid == NULL || ssid[0] == '\0') {
        ESP_LOGE(TAG, "未配置 Wi-Fi。请 idf.py menuconfig → \"zjy13 语音项目\"");
        app_ui_show_error("wifi_config", "未配置 Wi-Fi");
        return;
    }

    app_ui_set_status("连接 Wi-Fi...");
    esp_err_t err = app_net_wifi_init(ssid, pass, APP_WIFI_TIMEOUT_MS);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Wi-Fi 连接失败: %s（继续等待自动重连）",
                 esp_err_to_name(err));
        app_ui_show_error("wifi_failed", "Wi-Fi 连不上，检查 SSID/2.4GHz");
    } else {
        char status[64];
        snprintf(status, sizeof(status), "Wi-Fi %s", app_net_ip());
        app_ui_set_status(status);
    }

    /* 4) 音频。失败不阻断网络链路，但必须显式报出来 */
    err = app_audio_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "音频初始化失败: %s（协议可工作，但无法录音/播放）",
                 esp_err_to_name(err));
        app_ui_show_error("audio_init", "音频初始化失败");
    }

    /* 5) WebSocket */
    app_ws_set_callbacks(on_ws_text, on_ws_bin, on_ws_state, NULL);
    err = app_ws_start(APP_SERVER_HOST, APP_SERVER_PORT, "/");
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "WebSocket 启动失败: %s", esp_err_to_name(err));
        app_ui_show_error("ws_start", "WebSocket 启动失败");
        return;
    }

    xTaskCreate(heartbeat_task, "heartbeat", 3072, NULL, 3, NULL);

    app_ui_set_hint("等待连接...");

    /*
     * 输入通道：优先 USB Serial/JTAG（本板通过原生 USB 连接），
     * 其次 UART0。二者不能用同一个驱动重复安装，
     * 因此先尝试 USB，失败再退回 UART0。
     *
     * 命令（单字符）：R=录音 S=停止 C=取消 P=状态
     */
    ESP_LOGI(TAG, "就绪。命令：R=录音 S=停止 C=取消 P=状态");

    int input_kind = 0;   /* 1 = USB JTAG, 2 = UART0 */

    {
        usb_serial_jtag_driver_config_t usb_cfg =
            USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
        usb_cfg.rx_buffer_size = 256;
        if (usb_serial_jtag_driver_install(&usb_cfg) == ESP_OK) {
            input_kind = 1;
            ESP_LOGI(TAG, "输入通道：USB Serial/JTAG");
        }
    }

    if (input_kind == 0) {
        const uart_port_t uart_num = UART_NUM_0;
        uart_config_t uart_cfg = {
            .baud_rate = 115200,
            .data_bits = UART_DATA_8_BITS,
            .parity = UART_PARITY_DISABLE,
            .stop_bits = UART_STOP_BITS_1,
            .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
            .source_clk = UART_SCLK_DEFAULT,
        };
        uart_param_config(uart_num, &uart_cfg);
        esp_err_t uerr = uart_driver_install(uart_num, 256, 0, 0, NULL, 0);
        if (uerr == ESP_OK) {
            input_kind = 2;
            ESP_LOGI(TAG, "输入通道：UART0（115200）");
        } else {
            ESP_LOGE(TAG, "UART0 驱动安装失败: %s（可能已被 console 占用）",
                     esp_err_to_name(uerr));
        }
    }

    if (input_kind == 0) {
        ESP_LOGE(TAG, "没有可用输入通道，仅能通过日志观察状态");
    }

    uint8_t cmd = 0;
    while (true) {
        int n = 0;
        if (input_kind == 1) {
            n = usb_serial_jtag_read_bytes(&cmd, 1, pdMS_TO_TICKS(200));
        } else if (input_kind == 2) {
            n = (int)uart_read_bytes(UART_NUM_0, &cmd, 1,
                                     pdMS_TO_TICKS(200));
        } else {
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }
        if (n <= 0) {
            continue;
        }

        switch (cmd) {
        case 'R':
        case 'r':
            start_recording();
            break;
        case 'S':
        case 's':
            stop_recording();
            break;
        case 'C':
        case 'c':
            if (app_state_busy()) {
                char buf[128];
                if (app_proto_build_cancel(buf, sizeof(buf), s_rt.turn_id) >= 0) {
                    app_ws_send_text(buf, strlen(buf));
                }
                ESP_LOGI(TAG, "已发送 cancel");
            }
            break;
        case 'P':
        case 'p':
            ESP_LOGI(TAG, "状态=%s 连接=%d Wi-Fi=%d 帧=%u 字节=%u",
                     app_state_name(app_state_get()), (int)app_ws_connected(),
                     (int)app_net_wifi_ready(),
                     (unsigned)s_rt.recv_frames, (unsigned)s_rt.recv_bytes);
            break;
        default:
            if (cmd != '\r' && cmd != '\n') {
                ESP_LOGW(TAG, "未知命令 '%c'（可用: R/S/C/P）", cmd);
            }
            break;
        }
    }
}
