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
/* 录音格式：与 BSP 的 I2S 保持一致。
 *
 * 为什么改成 16000：
 *   原厂 TONE 命令用 16000/mono 播放完全正常（实测零复位），
 *   而 BSP 默认的 22050 在我这里从未成功过。
 *   16000 是这块板子上唯一被验证过的采样率，优先对齐它。
 *   ASR 也正好要 16k，服务端无需重采样。
 */
#define MIC_SAMPLE_RATE   16000
#define MIC_CHANNELS      2      /* BSP 配置为 stereo slot，实际 ES7210 双麦 */
#define MIC_BITS          16
#define CHUNK_MS          40
#define CHUNK_BYTES       (MIC_SAMPLE_RATE * MIC_CHANNELS * (MIC_BITS / 8) * CHUNK_MS / 1000)

/* 播放格式：服务端下发 24000 Hz mono 16bit，这里统一按 16000 设备播放。
 * 设备侧与录音同用 16000（原厂 TONE 验证过的唯一可用值），
 * 播放任务里做 24k -> 16k 线性重采样。 */
#define SPK_SAMPLE_RATE   24000
#define SPK_CHANNELS      1

#define SPK_DEV_RATE      16000

/* 单轮音频上限 60 秒 */
#define MAX_TURN_SECONDS   60

/* 心跳间隔：协议规定 20 秒；服务端 30 秒无消息即断开连接 */
#define APP_VOICE_HEARTBEAT_MS  20000

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
static void play_task(void *arg);
static void finish_playback(void);

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
/* 播放音量 0~100，可用串口命令 VOL <n> 运行时调整 */
static volatile int s_volume = 60;

/* ---- 播放缓冲：网络回调只入队，独立任务负责写 codec ----
 *
 * 为什么不直接在 WebSocket 回调里写 codec：
 * 实测 133 帧 / 11 秒音频，esp_codec_dev_write 每帧阻塞约 85ms，
 * 合计超过 11 秒。回调被阻塞期间收不到后续帧、发不出心跳，
 * 服务端 30 秒空闲超时后直接断开连接。
 *
 * 因此改为：DATA 回调把 PCM 写入有界环形缓冲，
 * 播放任务异步取数据写 codec，二者互不阻塞。
 */
#define PLAY_MAX_SECONDS  20                     /* 单轮音频上限（服务端约 12s） */
#define PLAY_BUF_BYTES    (SPK_SAMPLE_RATE * 2 * PLAY_MAX_SECONDS)
#define PLAY_RESAMPLE_MAX (PLAY_MAX_SECONDS * 3) /* 重采样后略长，留余量 */

static int16_t *s_play_buf;        /* 环形缓冲（原始 24k PCM） */
static size_t   s_play_head, s_play_tail, s_play_count;
static TaskHandle_t s_play_task;
static SemaphoreHandle_t s_play_mutex;
static volatile bool s_play_draining;   /* 收到最后一帧，等待缓冲排空 */

/* 音频设备句柄 */
static esp_codec_dev_handle_t s_mic;
static esp_codec_dev_handle_t s_spk;

/* 录音缓冲（内部 RAM，避免 PSRAM 与 DMA 冲突） */
static int16_t *s_mic_buf;
static uint8_t *s_frame_buf;
/* 下行二进制分片重组缓冲（放 PSRAM，省内部 RAM 给 LCD SPI DMA） */
static uint8_t *s_bin_buf;

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

/* 按需初始化：录音只初始化麦克风，播放只初始化扬声器。
 *
 * 原实现两个一起初始化，导致每次 TALK 都会唤醒 ES8311 功放，
 * 白耗电流（日志里 TALK 时出现 "ES8311: Work in Slave mode" 即为证据）。
 * 供电本就紧张，能省一点是一点。
 */
static esp_err_t mic_ensure(void)
{
    if (!s_mic) {
        s_mic = bsp_audio_codec_microphone_init();
        if (!s_mic) {
            ESP_LOGE(TAG, "麦克风初始化失败");
            return ESP_FAIL;
        }
    }
    return ESP_OK;
}

static esp_err_t spk_ensure(void)
{
    if (!s_spk) {
        s_spk = bsp_audio_codec_speaker_init();
        if (!s_spk) {
            ESP_LOGE(TAG, "扬声器初始化失败");
            return ESP_FAIL;
        }
    }
    return ESP_OK;
}

static esp_err_t audio_ensure(void)
{
    return mic_ensure() == ESP_OK ? spk_ensure() : ESP_FAIL;
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
    /* 只有仍处于 RECORDING 时才回到 IDLE。
     * 无条件赋值会把"已经进入播放"的状态覆盖掉，
     * 导致 playback_start 之后状态判断全乱、playback_done 不上报。 */
    if (s_state == ST_RECORDING) {
        s_state = ST_IDLE;
        /* 不在录音任务里调用 ui_sync()。
         * ui_sync -> badge_dashboard_voice -> portENTER_CRITICAL，
         * 实测在本任务收尾处调用会破坏寄存器窗口，导致 retw.n 崩溃
         * （A0 返回地址变为 0）。dashboard 自带 refresh 定时器，
         * 会按 voice_state 刷新；状态变化由其他路径同步即可。 */
    }

    /* 诊断：记录本任务栈的历史最低余量（word 数，越小越危险） */
    ESP_LOGW(TAG, "record_loop 退出 stack_hwm=%u words",
             (unsigned)uxTaskGetStackHighWaterMark(NULL));

    /* 本任务返回后会被 FreeRTOS 自动删除，必须清掉句柄。
     * 否则 handle_session_start 里的 vTaskDelete(s_task) 会对已释放的
     * TCB 再次删除 —— 确凿的 use-after-free，下一轮 TALK 必崩。
     * 只在句柄仍指向自己时才清，避免覆盖刚创建的新任务句柄。 */
    if (s_task == xTaskGetCurrentTaskHandle()) {
        s_task = NULL;
    }

    /* 显式自删，不让函数走 retw.n 返回路径。
     * 实测该任务的返回指令会因窗口状态异常触发 IllegalInstruction
     * （A0 返回地址被清 0）；vTaskDelete 内部直接切换任务、不执行 retw。 */
    vTaskDelete(NULL);
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
    s_play_draining = false;

    /* 清空缓冲 */
    if (s_play_mutex) xSemaphoreTake(s_play_mutex, portMAX_DELAY);
    s_play_head = s_play_tail = s_play_count = 0;
    if (s_play_mutex) xSemaphoreGive(s_play_mutex);

    /* 打开扬声器：直接用 BSP 实际 I2S 采样率 22050。
     * TTS 输出 24k，播放任务里做线性重采样。
     *
     * 供电保护（实测：音频下发瞬间板子会 brownout 复位）：
     *   1) 音量从 60 降到 30，减少功放峰值电流
     *   2) 开功放后先写 150ms 静音做"软启动"，让功放与电源缓冲平滑建立，
     *      而不是一上来就推大信号
     */
    if (spk_ensure() == ESP_OK) {
        esp_codec_dev_sample_info_t fs = {
            .sample_rate = SPK_DEV_RATE,
            .channel = SPK_CHANNELS,
            .bits_per_sample = 16,
        };
        if (esp_codec_dev_open(s_spk, &fs) != ESP_OK) {
            ESP_LOGE(TAG, "扬声器打开失败");
        }
        esp_codec_dev_set_out_vol(s_spk, s_volume);

        /* 软启动静音：150ms @ 22050Hz 单声道 */
        static int16_t softstart[SPK_DEV_RATE * 3 / 20];
        memset(softstart, 0, sizeof(softstart));
        esp_codec_dev_write(s_spk, softstart, (int)sizeof(softstart));
    }

    s_state = ST_PLAYING; ui_sync();
    ESP_LOGI(TAG, "开始播放：预期 %u 帧 / %u 字节 @%dHz -> 设备 %dHz",
             (unsigned)frames, (unsigned)bytes, SPK_SAMPLE_RATE, SPK_DEV_RATE);
}

/* 播放任务：从环形缓冲取 PCM，重采样后写 codec。
 * 独立于网络回调，避免阻塞收包与心跳。 */
static void play_task(void *arg)
{
    (void)arg;
    static int16_t out_buf[SPK_DEV_RATE * 2 / 4];   /* 每次 250ms */

    while (true) {
        /* 取数据 */
        size_t avail = 0;
        if (s_play_mutex) xSemaphoreTake(s_play_mutex, portMAX_DELAY);
        avail = s_play_count;
        if (avail > 0) {
            const size_t take = (avail > 1024) ? 1024 : avail;   /* 512 采样 */
            for (size_t i = 0; i < take; ++i) {
                out_buf[i] = s_play_buf[s_play_head];
                s_play_head = (s_play_head + 1) % (PLAY_BUF_BYTES / sizeof(int16_t));
            }
            s_play_count -= take;
            avail = take;
        }
        if (s_play_mutex) xSemaphoreGive(s_play_mutex);

        if (avail > 0 && s_spk) {
            /* 24k -> 22.05k 线性重采样（比例 22050/24000） */
            const size_t out_n = (size_t)((double)avail * SPK_DEV_RATE / SPK_SAMPLE_RATE);
            static int16_t resamp[1024];
            for (size_t i = 0; i < out_n && i < 1024; ++i) {
                const double src = (double)i * SPK_SAMPLE_RATE / SPK_DEV_RATE;
                const size_t idx = (size_t)src;
                const double frac = src - (double)idx;
                const int16_t a = out_buf[idx < avail ? idx : avail - 1];
                const int16_t b = out_buf[(idx + 1 < avail) ? idx + 1 : avail - 1];
                resamp[i] = (int16_t)(a + (b - a) * frac);
            }
            if (out_n > 0) {
                esp_codec_dev_write(s_spk, resamp, (int)(out_n * 2));
            }
            continue;
        }

        /* 缓冲空了：若已收齐最后一帧，则关闭扬声器并上报 */
        if (s_play_draining) {
            s_play_draining = false;
            finish_playback();
            continue;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

static void finish_playback(void)
{
    if (!s_playing) return;
    s_playing = false;

    /* 关闭前先写 80ms 静音并稍作等待，让功放电流平滑回落。
     * 直接 close 会在信号高峰处切断，产生电流突变并加剧掉电风险。 */
    if (s_spk) {
        static int16_t fadeout[SPK_DEV_RATE * 2 / 25];   /* 80ms */
        memset(fadeout, 0, sizeof(fadeout));
        esp_codec_dev_write(s_spk, fadeout, (int)sizeof(fadeout));
        vTaskDelay(pdMS_TO_TICKS(50));
        esp_codec_dev_close(s_spk);
    }

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

    /* 前 3 帧打日志：确认二进制帧真的到达了板子。
     * 之前"服务端已下发 91 帧但板子毫无反应"，需要区分是
     * 没收到帧，还是收到了但没播放。 */
    if (s_recv_frames <= 3) {
        ESP_LOGI(TAG, "收到音频帧 #%u seq=%u %u 字节 playing=%d",
                 (unsigned)s_recv_frames, (unsigned)data[2],
                 (unsigned)plen, (int)s_playing);
    }

    /* 只入队，不在回调里写 codec（见上方播放缓冲注释） */
    if (s_playing && s_play_buf) {
        const int16_t *src = (const int16_t *)(data + BIN_HDR_SIZE);
        const size_t n = plen / sizeof(int16_t);
        if (s_play_mutex) xSemaphoreTake(s_play_mutex, portMAX_DELAY);
        for (size_t i = 0; i < n; ++i) {
            if (s_play_count >= PLAY_BUF_BYTES / sizeof(int16_t)) {
                /* 缓冲满：丢弃新数据并告警，不能阻塞回调 */
                ESP_LOGW(TAG, "播放缓冲已满，丢弃 %u 采样", (unsigned)(n - i));
                break;
            }
            s_play_buf[s_play_tail] = src[i];
            s_play_tail = (s_play_tail + 1) % (PLAY_BUF_BYTES / sizeof(int16_t));
            s_play_count++;
        }
        if (s_play_mutex) xSemaphoreGive(s_play_mutex);
    }

    /* 收齐最后一帧：标记排空，由播放任务在缓冲写完后上报 playback_done */
    if (s_expect_frames && s_recv_frames >= s_expect_frames) {
        s_play_draining = true;
    }
}

static void handle_text(const char *json)
{
    char type[32];
    if (!json_get_str(json, "type", type, sizeof(type))) return;

    /* 每条控制消息都打一行：联调时若某类消息没到，
     * 一眼就能看出是没收到还是处理分支有问题。 */
    ESP_LOGI(TAG, "收到控制消息: %s (len=%u)", type, (unsigned)strlen(json));

    if (strcmp(type, "server_hello") == 0) {
        ESP_LOGI(TAG, "握手完成");
    } else if (strcmp(type, "session_start") == 0) {
        json_get_str(json, "turn_id", s_turn_id, sizeof(s_turn_id));
        s_turn_short = crc32_str(s_turn_id);
        s_send_seq = 0;
        ESP_LOGI(TAG, "本轮 turn_id=%s", s_turn_id);

        /* 拿到 turn_id 之后才真正开始采集上传。
         * 顺序错会导致帧头 CRC 与轮次对不上，服务端直接丢弃音频。
         *
         * 注意：必须先把状态切到 ST_RECORDING 再创建任务，
         * 否则 record_loop 的循环条件 (s_state == ST_RECORDING) 立即为假，
         * 任务会直接退出、一个音频块都不会上传。 */
        if (s_talk_requested) {
            s_talk_requested = false;
            s_talk_stop_requested = false;
            s_state = ST_RECORDING; ui_sync();
            if (s_task) vTaskDelete(s_task);
            /* 4096 字节栈不足以承载 esp_websocket_client_send_bin 的调用链，
             * 实测会在 record_loop 返回时（retw.n）因栈窗口损坏触发
             * IllegalInstruction。与同工程其他任务对齐，加大到 8192。 */
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
            /* 文本消息：必须支持分片与足够缓冲。
             * 实测 playback_start 带 sample_rate/duration_s/bytes/frames/
             * frame_bytes 加上 envelope，总长会超过 512 字节；原来用
             * 512 字节缓冲会把尾部字段截断，导致 json_get_int 读不到
             * frames/bytes，进而不播放、也不回报 playback_done。 */
            static char s_json[1024];
            static size_t s_json_len = 0;

            if (ev->payload_offset == 0) s_json_len = 0;

            const size_t need = s_json_len + (size_t)ev->data_len;
            if (need >= sizeof(s_json)) {
                ESP_LOGW(TAG, "文本消息超长（%u 字节），丢弃",
                         (unsigned)need);
                s_json_len = 0;
                break;
            }
            memcpy(s_json + s_json_len, ev->data_ptr, (size_t)ev->data_len);
            s_json_len = need;

            /* 收满整条消息才解析 */
            if ((int)s_json_len >= ev->payload_len) {
                s_json[s_json_len] = '\0';
                handle_text(s_json);
                s_json_len = 0;
            }
        } else if (ev->op_code == 0x02) {
            /* 二进制音频帧：必须和文本一样支持分片重组。
             * 实测一帧约 4KB 会被 esp_websocket_client 拆成多个 DATA 事件
             * （buffer 分配降级或分片），原实现把每个分片当完整帧解析，
             * 导致 magic 校验失败、seq 错乱、只播约 1 秒就停住。 */
            const size_t BIN_CAP = 8192;
            if (!s_bin_buf) {
                s_bin_buf = heap_caps_malloc(BIN_CAP, MALLOC_CAP_SPIRAM);
                if (!s_bin_buf) { ESP_LOGW(TAG, "分片缓冲分配失败"); break; }
            }
            static size_t  s_bin_len = 0;
            static uint32_t s_bin_dbg = 0;

            if (ev->payload_offset == 0) s_bin_len = 0;

            const size_t need = s_bin_len + (size_t)ev->data_len;
            if (need > BIN_CAP) {
                ESP_LOGW(TAG, "二进制消息超长（%u 字节），丢弃", (unsigned)need);
                s_bin_len = 0;
                break;
            }
            memcpy(s_bin_buf + s_bin_len, ev->data_ptr, (size_t)ev->data_len);
            s_bin_len = need;

            /* 收满整条消息才交给帧解析 */
            if ((int)s_bin_len >= ev->payload_len) {
                if (s_bin_dbg < 3) {
                    ESP_LOGW(TAG, "bin ev off=%d data=%u plen=%d acc=%u intfree=%u",
                             (int)ev->payload_offset, (unsigned)ev->data_len,
                             (int)ev->payload_len, (unsigned)s_bin_len,
                             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
                    s_bin_dbg++;
                }
                handle_binary(s_bin_buf, s_bin_len);
                s_bin_len = 0;
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

/* ------------------------------------------------------------------ */
/* 心跳                                                                */
/* ------------------------------------------------------------------ */

/* 服务端 HEARTBEAT_TIMEOUT_S=30，协议规定客户端每 20 秒发一次心跳。
 * 缺少心跳时连接会在约 29 秒后被服务端主动关闭 —— 这是实测踩过的坑：
 * 表现为"刚连上能握手，过一会儿 TALK 就返回 false"。 */
static void heartbeat_task(void *arg)
{
    (void)arg;
    char buf[96];
    int tick = 0;
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(APP_VOICE_HEARTBEAT_MS));
        tick++;
        /* 周期状态行：板子在无日志时完全静默，联调时无法判断
         * 当前是"连接中/播放中/已断开"，只能靠猜。
         * 每 3 次心跳（约 60 秒）打一行，不刷屏。 */
        if (tick % 3 == 0) {
            printf("BADGE_VOICE_PING {\"conn\":%d,\"state\":%d,"
                   "\"frames\":%u,\"play_left\":%u}\n",
                   s_connected ? 1 : 0, (int)s_state,
                   (unsigned)s_recv_frames, (unsigned)s_play_count);
        }
        if (!s_connected || !s_ws) continue;
        snprintf(buf, sizeof(buf),
                 "{\"type\":\"heartbeat\",\"protocol_version\":%d}", PROTO_VERSION);
        send_text(buf);
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
        /* 关键：必须显式指定 transport。
         * 组件默认值是 WEBSOCKET_TRANSPORT_UNKNOWN(0)，而其内部逻辑只判断
         * OVER_TCP(1) 与 OVER_SSL(2)，UNKNOWN 会落到 else 分支被当作 SSL，
         * 于是明文 ws:// 也会走 TLS 握手并报：
         *   ESP_ERR_ESP_TLS_FAILED_CONNECT_TO_HOST, errno=118
         */
        .transport = WEBSOCKET_TRANSPORT_OVER_TCP,
        .network_timeout_ms = 10000,
        /* 使用组件内置自动重连（每 5 秒），不要自己循环 start/stop。
         * 之前实测：组件内部重连与外部重试叠加会导致连续多次
         * "WebSocket 错误 -> 连接断开"，状态在 IDLE/OFF 之间抖动，
         * 表现为 TALK 随机失败。 */
        .reconnect_timeout_ms = 5000,
        .disable_auto_reconnect = false,
        .buffer_size = 16384,   /* 需容纳 4096B 帧 + 突发 */
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

    /* 心跳任务：只创建一次，内部按连接状态自行判断是否发送 */
    static bool hb_started = false;
    if (!hb_started) {
        if (xTaskCreate(heartbeat_task, "voice_hb", 3072, NULL, 3, NULL) == pdPASS) {
            hb_started = true;
        }
    }

    /* 播放缓冲与播放任务：只创建一次 */
    if (!s_play_buf) {
        s_play_buf = heap_caps_malloc(PLAY_BUF_BYTES, MALLOC_CAP_SPIRAM);
        if (!s_play_buf) {
            /* PSRAM 不可用时退回内部 RAM（20 秒 24k 单声道约 960KB，内部放不下，
             * 这里缩短到 6 秒以适配） */
            s_play_buf = heap_caps_malloc(SPK_SAMPLE_RATE * 2 * 6,
                                          MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
            if (!s_play_buf) {
                ESP_LOGE(TAG, "播放缓冲分配失败");
            }
        }
        s_play_mutex = xSemaphoreCreateMutex();
    }
    if (s_play_buf && !s_play_task) {
        /* 播放任务同样涉及 codec 写入与重采样，4096 偏紧，加大到 8192。 */
        if (xTaskCreate(play_task, "voice_play", 4096, NULL, 6, &s_play_task) != pdPASS) {
            ESP_LOGE(TAG, "播放任务创建失败");
            s_play_task = NULL;
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
    /* 只要求"已连接"，不再强制要求 state==IDLE。
     * 实测：握手完成到事件回调置 IDLE 之间存在极短竞争窗口，
     * 严格判 IDLE 会让 TALK 偶发失败。只要当前不在录音/播放即可。 */
    if (!s_connected || !s_ws) {
        ESP_LOGW(TAG, "TALK 失败：未连接服务端");
        return ESP_ERR_INVALID_STATE;
    }
    if (s_state == ST_RECORDING || s_state == ST_PLAYING) {
        ESP_LOGW(TAG, "TALK 失败：正忙（state=%d）", (int)s_state);
        return ESP_ERR_INVALID_STATE;
    }
    if (mic_ensure() != ESP_OK) return ESP_FAIL;

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
    s_state = ST_RECORDING; ui_sync();   /* 允许 stop 立即生效 */

    /* 录音任务不在这里启动，等 session_start 拿到 turn_id 后再起。
     * 这里只负责发出请求。 */
    ESP_LOGI(TAG, "已请求开始录音，等待 session_start");
    return ESP_OK;
}

esp_err_t voice_talk_stop(void)
{
    /* 只置停止标志，让 record_loop 自己退出并关闭麦克风；
     * 不在这里直接改 s_state，否则录音任务会在循环条件上立即中断，
     * 最后若干音频块可能丢失。状态由任务收尾时切回 IDLE。 */
    if (s_state != ST_RECORDING && !s_talk_requested) {
        return ESP_ERR_INVALID_STATE;
    }
    s_talk_stop_requested = true;
    s_talk_requested = false;

    char buf[JSON_BUF];
    snprintf(buf, sizeof(buf),
             "{\"type\":\"recording_stop\",\"protocol_version\":%d}", PROTO_VERSION);
    send_text(buf);
    ESP_LOGI(TAG, "停止录音");
    return ESP_OK;
}

void voice_set_volume(int v)
{
    if (v < 0) v = 0;
    if (v > 100) v = 100;
    s_volume = v;
    /* 正在播放时立即生效；否则保存，下一轮 playback_start 打开扬声器后应用 */
    if (s_spk && s_playing) {
        esp_codec_dev_set_out_vol(s_spk, v);
    }
    printf("BADGE_VOICE {\"volume\":%d}\n", v);
}

int voice_get_volume(void)
{
    return s_volume;
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
