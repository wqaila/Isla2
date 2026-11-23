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
#include "esp_timer.h"
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

/* 播放格式：服务端下发 24000 Hz mono 16bit。
 * 设备侧直接以 24000 播放，不做重采样。
 * 原先设备跑 16000 需要 24k->16k 降采样，带来三个问题：
 *   1) 8~12kHz 频段被整体丢弃 → 声音发闷、缺高频细节（音色不像的主因之一）
 *   2) 线性插值无抗混叠滤波 → 8kHz 以上折返成谐波失真，听感发毛
 *   3) 重采样相位每块重置（src 从 0 重算）→ 每约 43ms 一个断点
 * ES8311 支持 8k~48k；录音/播放是半双工，各自 open 时重配 I2S，互不冲突。 */
#define SPK_SAMPLE_RATE   24000
#define SPK_CHANNELS      1

#define SPK_DEV_RATE      SPK_SAMPLE_RATE

/* 单轮音频上限 60 秒 */
#define MAX_TURN_SECONDS   60

/* 最短有效录音时长（毫秒）。
 *
 * 为什么需要：LVGL 的 LV_EVENT_LONG_PRESSED 在**按住满 300ms 时才触发**，
 * 录音起点比手指按下晚 300ms。用户按 400ms 就松手 → 只录到约 100ms，
 * 而服务端 ASR 要求 ≥200ms，会直接报 asr_failed: 音频过短。
 *
 * 取 350ms 的理由：
 *   - 服务端下限 200ms，留 150ms 余量吸收线程调度与首块延迟
 *   - 中文说 2~3 个字约 400~600ms，350ms 不会误伤正常短句
 *   - 低于 350ms 的录音即使送上去，识别结果也基本是噪声 */
#define MIN_TALK_MS        350

/* 播放预缓冲（毫秒）。开播前先攒够这么多音频再写 codec。
 *
 * 为什么需要（2026-11-12）：流式下发下，首块音频到达时缓冲里只有一小段，
 * 原实现「有数据就立刻写」会立刻播空，然后 vTaskDelay(10ms) 等下一帧，
 * 用户听到的就是"刚开始播放卡一下"。
 *
 * 取 120ms 的依据：
 *   - 24k 下 120ms = 2880 采样，足够覆盖一次网络抖动 + 任务调度
 *   - 太小（40ms）挡不住 10ms 的轮询空档
 *   - 太大会增加首字延迟；用户明确要求低延迟，不能回退太多 */
#define PREBUF_MS          120

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
/* 强制收尾：不依赖 s_playing，保证发出 playback_done（2026-11-23） */
static void finish_playback_force(void);

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
/* 播放音量 0~100，可用串口命令 VOL <n> 运行时调整。
 * 默认 70（2026-11-23 由 85 下调，用户确认后固化）。
 *
 * 历史：60 在安静环境偏轻 → 提到 85 留 15 余量避免长按满幅削波破音
 * （实测 100 会破）。实测 85 对当前角色素材偏响，70 更均衡。
 * 觉得仍偏小可串口发 VOL 78；觉得破音则发 VOL 65。
 *
 * 注意：该值只在编译时生效。运行期 VOL <n> 调整不写 NVS，
 * 重新上电会回到此默认值。 */
static volatile int s_volume = 70;

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
/* 单轮音频上限。服务端已改为按播放进度节流下发（领先约 1 秒），
 * 缓冲不再需要容纳整段音频，8 秒足够吸收抖动，同时降低 PSRAM 占用与
 * 长音频时的高水位（20 秒缓冲曾导致 LCD 的 SPI DMA 抢不到内存而报
 * ESP_ERR_NO_MEM）。 */
#define PLAY_MAX_SECONDS  8
#define PLAY_BUF_BYTES    (SPK_SAMPLE_RATE * 2 * PLAY_MAX_SECONDS)
#define PLAY_RESAMPLE_MAX (PLAY_MAX_SECONDS * 3) /* 重采样后略长，留余量 */

static int16_t *s_play_buf;        /* 环形缓冲（原始 24k PCM） */
static size_t   s_play_head, s_play_tail, s_play_count;
static TaskHandle_t s_play_task;
static SemaphoreHandle_t s_play_mutex;
static volatile bool s_play_draining;   /* 收到最后一帧，等待缓冲排空 */
/* 流式播放：服务端不再预先告知总帧数，改为在所有音频后发一个
 * 「负载长度为 0」的二进制帧作为结束标记。收到它才标记排空。
 * 定长模式（playback_start 带 frames>0）仍然走原来的帧数判断，
 * 两种模式并存，旧服务端不受影响。 */
static volatile bool s_got_end_marker;
/* 播放任务的空闲计数：用于流式模式下检测「服务端中途断流」 */
static uint32_t s_idle_ticks;
/* 收到结束标记后的收尾兜底计数（2026-11-23）。
 * 用于修复「音频已播完但状态机卡在 PLAYING、playback_done 不发」。 */
static uint32_t s_end_grace_ticks;
/* 录音请求在途计数（秒）。voice_talk_start 发出 recording_start 后，
 * 若迟迟收不到 session_start，用它兜底复位，避免「在途」标志永久卡死。
 * 2026-11-23 新增。 */
static uint32_t s_talk_pending_ticks;
/* 预缓冲是否已完成（每轮播放开始时由 handle_playback_start 复位）。
 * 播放任务是常驻的，用静态变量才能跨轮次正确重置。 */
static volatile bool s_prebuf_done;
/* 本轮录音真正开始的时刻（us，esp_timer）。
 * 用于最短时长保护：LVGL 长按事件在按住 300ms 后才触发，
 * 若用户很快松手，实际录音窗口可能只有几十毫秒。 */
static int64_t s_record_start_us;

/* 下行文本（ASR 结果 / LLM 回复 / delta）的共享缓冲。
 *
 * 为什么用静态而不是栈数组：
 * 1) 原实现 asr_final 用 char[160]、llm_final 用 char[240]，
 *    **装不下完整内容**——中文 UTF-8 每字 3 字节，240 字节只够 80 字，
 *    而 LLM_MAX_TOKENS=128 约 85 汉字（≈255 字节），实测尾巴被截断。
 * 2) 但这些函数跑在 WebSocket 事件任务的栈上，该任务只有 4096 字节。
 *    把数组加到 768 会明显加大栈压力（该任务历史上有过崩溃）。
 * 3) handle_text 只由 WebSocket 事件回调单线程调用，无并发风险，
 *    因此共享一块静态缓冲是安全的，且不占栈。
 *
 * 1024 字节可容纳约 340 汉字，足够覆盖服务端 MAX_TEXT_CHARS 的常规取值。 */
static char s_text_buf[1024];

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

    /* 必须做转义解码（2026-11-12 修复）。
     *
     * 原实现是原样拷贝，导致服务端发来的 \n、\"、\\ 等序列
     * 会以字面量形式出现在屏幕与串口上。实测 LLM 回复里常带 \n
     * （角色卡允许换行），屏幕会显示成两个字符 "\n" 而不是换行。
     *
     * 这里处理 JSON 标准转义：\" \\ \/ \b \f \n \r \t 与 \uXXXX。
     * \uXXXX 只处理 BMP（中文都在此范围），按 UTF-8 编码写出；
     * 代理对（emoji 等）转成 U+FFFD，避免写出非法 UTF-8。 */
    size_t o = 0;
    while (*v && o + 1 < cap) {
        if (*v == '"' ) break;                 /* 未转义的引号 = 结束 */
        if (*v != '\\') { out[o++] = *v++; continue; }

        /* 遇到反斜杠：看下一个字符 */
        const char esc = v[1];
        if (esc == '\0') break;
        v += 2;
        switch (esc) {
        case '"':  out[o++] = '"';  break;
        case '\\': out[o++] = '\\'; break;
        case '/':  out[o++] = '/';  break;
        case 'b':  out[o++] = '\b'; break;
        case 'f':  out[o++] = '\f'; break;
        case 'n':  out[o++] = '\n'; break;
        case 'r':  out[o++] = '\r'; break;
        case 't':  out[o++] = '\t'; break;
        case 'u': {
            /* \uXXXX：解析 4 位十六进制 */
            unsigned cp = 0;
            int k = 0;
            for (; k < 4; ++k) {
                const char h = v[k];
                unsigned d;
                if (h >= '0' && h <= '9') d = (unsigned)(h - '0');
                else if (h >= 'a' && h <= 'f') d = (unsigned)(h - 'a' + 10);
                else if (h >= 'A' && h <= 'F') d = (unsigned)(h - 'A' + 10);
                else break;
                cp = cp * 16 + d;
            }
            if (k < 4) { out[o++] = '?'; break; }   /* 非法序列 */
            v += 4;
            if (cp >= 0xD800 && cp <= 0xDFFF) {
                /* 代理对：固件不拼 emoji，用替代字符避免非法 UTF-8 */
                cp = 0xFFFD;
            }
            /* 编码成 UTF-8 */
            if (cp < 0x80) {
                if (o + 1 < cap) out[o++] = (char)cp;
            } else if (cp < 0x800) {
                if (o + 2 < cap) {
                    out[o++] = (char)(0xC0 | (cp >> 6));
                    out[o++] = (char)(0x80 | (cp & 0x3F));
                }
            } else {
                if (o + 3 < cap) {
                    out[o++] = (char)(0xE0 | (cp >> 12));
                    out[o++] = (char)(0x80 | ((cp >> 6) & 0x3F));
                    out[o++] = (char)(0x80 | (cp & 0x3F));
                }
            }
            break;
        }
        default:
            /* 未知转义：原样保留反斜杠与字符，便于排查 */
            if (o + 2 < cap) { out[o++] = '\\'; out[o++] = esc; }
            break;
        }
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

    /* 记录「真正能开始读麦克风」的时刻。
     * 注意不能用 voice_talk_start 的时刻：那时只是发出了 recording_start，
     * 还要等 session_start 回来才启动本任务，中间有几十毫秒。
     * 用这个时间戳算录音时长才准确。 */
    s_record_start_us = esp_timer_get_time();

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
        /* 直接写 dashboard 的 voice_state（等价于 ui_sync 对 ST_IDLE 的映射），
         * 让屏幕在 100ms 内由 refresh 定时器刷成"语音待命"。
         * 这里刻意不走 ui_sync()：它是 switch 内联版本，历史上曾在函数尾部
         * 触发 retw.n 崩溃；改为独立的单次函数调用更稳。
         * 调用位置也放在 close 之前，远离函数尾部。 */
        badge_dashboard_voice(2);
    }

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
    s_got_end_marker = false;
    /* 复位预缓冲：本轮开播前要重新攒够 PREBUF_MS 音频 */
    s_prebuf_done = false;
    s_idle_ticks = 0;
    s_end_grace_ticks = 0;

    /* 清空缓冲 */
    if (s_play_mutex) xSemaphoreTake(s_play_mutex, portMAX_DELAY);
    s_play_head = s_play_tail = s_play_count = 0;
    if (s_play_mutex) xSemaphoreGive(s_play_mutex);

    /* 打开扬声器：设备与 TTS 同为 24k，直通不重采样。
     *
     * 供电保护（实测：音频下发瞬间板子会 brownout 复位）：
     *   1) 音量从 60 降到 30，减少功放峰值电流
     *   2) 开功放后先写 150ms 静音做"软启动"，让功放与电源缓冲平滑建立，
     *      而不是一上来就推大信号
     *
     * 2026-11-23 修复「用久后声音变尖（像快进）」：
     *
     *   现象：播放约 1.5 倍速、音调升高（24000/16000=1.5），重启板子即恢复。
     *
     *   根因：录音（ES7210，16kHz）与播放（ES8311，24kHz）是**两个独立
     *   codec 句柄**，但共用同一条 I2S 总线，而 I2S 时钟只有一个。
     *   esp_codec_dev_open() 开头有早退：
     *       if (dev->input_opened || dev->output_opened) return OK;
     *   一旦 s_spk 的 output_opened 因异常路径残留为 true，
     *   本次 open 直接返回，**set_fmt 不被调用** → I2S 时钟仍停在
     *   上次录音的 16kHz，而音频数据是 24kHz → 播成 1.5 倍速、音调变尖。
     *
     *   另一条路径：_i2s_data_enable() 在 RX 仍在工作时会把 TX 的 disable
     *   推迟（out_disable_pending），但 esp_codec_dev_close() 仍无条件把
     *   output_opened 置 false，造成「句柄认为已关、硬件其实还开着」。
     *
     *   修法：open 之前先 close 一次，强制把 output_opened 清成 false，
     *   保证本次 open 一定会走到 set_fmt，从而重配 I2S 时钟为 24kHz。
     *   代价极小（一次幂等调用），却能彻底消除这个状态残留。
     */
    if (spk_ensure() == ESP_OK) {
        esp_codec_dev_sample_info_t fs = {
            .sample_rate = SPK_DEV_RATE,
            .channel = SPK_CHANNELS,
            .bits_per_sample = 16,
        };
        /* 先关一次，清掉可能残留的 output_opened，避免 open 早退跳过 set_fmt */
        esp_codec_dev_close(s_spk);
        if (esp_codec_dev_open(s_spk, &fs) != ESP_OK) {
            ESP_LOGE(TAG, "扬声器打开失败");
        }
        /* 兜底：直接对共享 I2S 数据接口再设一次格式。
         *
         * 上面的 close+open 已能覆盖绝大多数情况，但 I2S 是 mic/spk 共享的
         * 单一时钟源，若某次录音的 16kHz 配置残留，仅靠 open 的 set_fmt
         * 未必能保证生效（open 内部仍有早退分支）。
         * 这里显式再调一次 data_if->set_fmt，把 I2S 时钟强制拉回 24kHz。
         * 该调用幂等，参数与 open 完全一致，不会引入副作用。 */
        {
            const audio_codec_data_if_t *data_if = bsp_audio_get_codec_itf();
            if (data_if && data_if->set_fmt) {
                data_if->set_fmt(data_if, ESP_CODEC_DEV_TYPE_OUT, &fs);
            }
        }
        esp_codec_dev_set_out_vol(s_spk, s_volume);

        /* 软启动静音：42ms @24k 单声道。
         * 原先按 SPK_DEV_RATE*3/20 开（24k 下 3600 采样 = 7.2KB 静态 RAM），
         * 内部 RAM 紧张时会挤掉 LCD 的 SPI DMA，改为固定 1024 采样足够。 */
        static int16_t softstart[1024];
        memset(softstart, 0, sizeof(softstart));
        esp_codec_dev_write(s_spk, softstart, (int)sizeof(softstart));
    }

    s_state = ST_PLAYING; ui_sync();
    /* frames==0 表示服务端走流式，总长度未知，靠结束标记帧收尾 */
    if (frames > 0) {
        ESP_LOGI(TAG, "开始播放（定长）：预期 %u 帧 / %u 字节 @%dHz -> 设备 %dHz",
                 (unsigned)frames, (unsigned)bytes, SPK_SAMPLE_RATE, SPK_DEV_RATE);
    } else {
        ESP_LOGI(TAG, "开始播放（流式）：长度未知，等待结束标记帧 @%dHz -> 设备 %dHz",
                 SPK_SAMPLE_RATE, SPK_DEV_RATE);
    }
}

/* 播放任务：从环形缓冲取 PCM，直接写 codec。
 * 独立于网络回调，避免阻塞收包与心跳。
 * 设备采样率与 TTS 一致（24k），无需重采样，频响完整保留。 */
static void play_task(void *arg)
{
    (void)arg;
    /* 每次最多取 1024 采样；原先按 SPK_DEV_RATE*2/4 开，白占 16KB 内部 RAM */
    static int16_t out_buf[1024];

    /* 预缓冲（2026-11-12 新增，修复"刚开始播放卡一下"）。
     *
     * 问题：原实现只要缓冲里有 1 个采样就立刻写 codec。流式下发下
     * 首块音频到达时缓冲里只有一小段，写出去立刻播空，
     * 然后 vTaskDelay(10ms) 等下一帧 —— 这 10ms 空档就是听到的卡顿。
     *
     * 修法：开播前先攒够 PREBUF_MS（120ms）音频再开始写。
     *
     * 状态放在静态变量 s_prebuf_done 里，由 handle_playback_start 复位：
     * play_task 是常驻任务，局部变量跨轮次不会重置。 */
    const size_t prebuf_need = (size_t)SPK_SAMPLE_RATE / 1000 * PREBUF_MS;   /* 采样数 */

    while (true) {
        /* 取数据 */
        size_t avail = 0;
        if (s_play_mutex) xSemaphoreTake(s_play_mutex, portMAX_DELAY);
        avail = s_play_count;

        /* 开播前预缓冲：不够就先不取，让网络回调继续填。
         * 若已收到结束标记（极短音频）则直接放行，避免死等。 */
        if (!s_prebuf_done) {
            if (avail < prebuf_need && !s_play_draining) {
                if (s_play_mutex) xSemaphoreGive(s_play_mutex);
                vTaskDelay(pdMS_TO_TICKS(5));
                continue;
            }
            s_prebuf_done = true;
        }

        if (avail > 0) {
            const size_t take = (avail > 1024) ? 1024 : avail;
            for (size_t i = 0; i < take; ++i) {
                out_buf[i] = s_play_buf[s_play_head];
                s_play_head = (s_play_head + 1) % (PLAY_BUF_BYTES / sizeof(int16_t));
            }
            s_play_count -= take;
            avail = take;
        }
        if (s_play_mutex) xSemaphoreGive(s_play_mutex);

        if (avail > 0 && s_spk) {
            /* 采样率一致：原样写出，保留 24kHz 完整频响 */
            esp_codec_dev_write(s_spk, out_buf, (int)(avail * 2));

            /* 2026-11-23 修复：写完必须让出 CPU，不能裸 continue。
             *
             * 实测症状：板子收了完整音频（126 帧 / 497744 字节），但
             * 服务端 36 秒收不到任何消息 → 心跳超时被断开；
             * 用户侧表现为「卡在正在回应、没有声音」。
             *
             * 根因：play_task 优先级 6（全工程最高），而原来这里
             * 只要缓冲里有数据就 `continue` 立刻进入下一轮取数，
             * **一次都不主动让出**。当 s_play_count 持续 >0 时，
             * 这个任务会长时间霸占 CPU，把优先级 3 的 heartbeat_task
             * 饿死 —— 心跳发不出去，服务端 30s 就判定连接失联并断开。
             *
             * 修法：每次写完让出 1 tick。
             *   - 1 tick = 10ms（configTICK_RATE_HZ=100），而一次
             *     esp_codec_dev_write 写 1024 采样 ≈ 42ms 音频，
             *     远大于 10ms，所以不会造成播放欠载；
             *   - 但足以让心跳任务与 WiFi 任务拿到 CPU。 */
            vTaskDelay(1);
            continue;
        }

        /* 缓冲空了：若已收到结束信号，则关闭扬声器并上报 */
        if (s_play_draining) {
            s_play_draining = false;
            finish_playback();
            continue;
        }

        /* 中途播空（预缓冲已用过）：下一段音频要重新攒，
         * 否则连续欠载时会反复「写一小段→播空」，听感更差。 */
        s_prebuf_done = false;

        /* 2026-11-23 修复：收到结束标记后的收尾兜底（基于 s_state，不看 s_playing）。
         *
         * 实测缺陷（两次复现）：板子 BADGE_VOICE 报 state=PLAYING、已收满 108 帧
         * / 427396 字节（与服务端完全一致），但 BADGE_STATUS 显示 playing=false，
         * 服务端等不到 playback_done，整轮永久卡在 playing。
         *
         * 关键：这里必须判断 s_state（语音状态机）而不是 s_playing（播放任务标志）。
         *   - s_playing=false 表示「播放任务认为没在播」，它可能已被提前清掉；
         *   - s_state=ST_PLAYING 表示「对外状态机仍认为在播」，正是卡住的表现。
         * finish_playback() 首行是 `if (!s_playing) return;`，一旦 s_playing
         * 提前为 false，它就直接返回 —— playback_done 不发、s_state 永不复位。
         *
         * 所以兜底条件是「s_state 仍是 PLAYING」且「已收到结束标记」，
         * 满足就强制走一次不依赖 s_playing 的收尾。
         * 取值 300 ticks = 3 秒：正常排空远小于此值。 */
        if (s_got_end_marker && s_state == ST_PLAYING) {
            if (s_end_grace_ticks++ >= 300) {   /* 300 * 10ms = 3s */
                s_end_grace_ticks = 0;
                ESP_LOGW(TAG, "结束标记已收到但 s_state 仍为 PLAYING，强制收尾"
                              "（s_playing=%d）", (int)s_playing);
                finish_playback_force();
                continue;
            }
        } else {
            s_end_grace_ticks = 0;
        }

        /* 流式兜底：如果长时间（12 秒）既没有新数据、也没收到结束标记，
         * 说明服务端中途断了。主动收尾回到 IDLE，避免永久卡在 PLAYING
         * 让后续 TALK 全部失败（该问题在 11-04 记录里出现过）。
         * 定长模式下 s_expect_frames>0，一般不会走到这里。 */
        if (s_playing && !s_got_end_marker) {
            if (s_idle_ticks++ >= 1200) {   /* 1200 * 10ms = 12s */
                s_idle_ticks = 0;
                ESP_LOGW(TAG, "流式播放超时无数据且无结束标记，强制收尾");
                finish_playback();
                continue;
            }
        } else {
            s_idle_ticks = 0;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

static void finish_playback(void)
{
    if (!s_playing) return;
    s_playing = false;

    /* 关闭前先写 42ms 静音并稍作等待，让功放电流平滑回落。
     * 直接 close 会在信号高峰处切断，产生电流突变并加剧掉电风险。 */
    if (s_spk) {
        static int16_t fadeout[1024];   /* 42ms @24k */
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

/* 强制收尾（2026-11-23）。
 *
 * 与 finish_playback() 的唯一区别：**不看 s_playing**，只看 s_state。
 *
 * 为什么需要：finish_playback() 首行 `if (!s_playing) return;`。
 * 实测存在 s_playing 已被提前清成 false、而 s_state 仍停在 ST_PLAYING 的
 * 悬挂状态：此时正常收尾直接返回，playback_done 永远不发，
 * 服务端看门狗要等 120 秒才强制复位，用户侧表现为「一直卡在正在回应」。
 *
 * 本函数保证：只要调用，就一定发出 playback_done 并把状态机复位。
 * 对 codec 的操作做了存在性判断，重复关闭是安全的（esp_codec_dev_close
 * 在未打开时直接返回 OK）。 */
static void finish_playback_force(void)
{
    ESP_LOGW(TAG, "强制收尾：s_playing=%d s_state=%d 帧=%u 字节=%u",
             (int)s_playing, (int)s_state,
             (unsigned)s_recv_frames, (unsigned)s_recv_bytes);

    s_playing = false;
    s_play_draining = false;

    /* 关闭前静音淡出，避免在信号高峰处切断 */
    if (s_spk) {
        static int16_t fadeout[1024];
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

    ESP_LOGW(TAG, "强制收尾已发送 playback_done：%u 帧 / %u 字节",
             (unsigned)s_recv_frames, (unsigned)s_recv_bytes);
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

    /* 结束标记帧：负载长度为 0。
     * 流式模式下服务端不知道总帧数，用这个空帧告知「后面没有了」。
     * 必须放在计数之前，否则会被算成「多收了一帧」。
     * 定长模式下服务端不会发这种帧，所以不影响旧路径。 */
    if (plen == 0) {
        s_got_end_marker = true;
        s_play_draining = true;
        ESP_LOGI(TAG, "收到结束标记帧：共 %u 帧 / %u 字节",
                 (unsigned)s_recv_frames, (unsigned)s_recv_bytes);
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

    /* 收尾判定，两种模式并存：
     * 1) 定长（旧服务端）：playback_start 带 frames>0，收够帧数即排空
     * 2) 流式（新服务端）：frames==0，靠前面那个空负载结束标记帧置位
     * 两条件都只在「确实进入了播放」时生效，避免空轮次误判。 */
    if (s_expect_frames && s_recv_frames >= s_expect_frames) {
        s_play_draining = true;
    }
    /* 流式模式下若服务端漏发结束标记，靠看门狗兜底（见 s_playing 超时） */
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
            /* 2026-11-23 修复：栈从 4096 改为 8192。
             *
             * 上面的注释一直写着"加大到 8192"，但实际参数仍是 4096 ——
             * 注释与代码不一致，导致这个问题一直没真正修掉。
             *
             * 实测症状：**松开屏幕的瞬间**板子就断开 WebSocket 重连。
             * 原因：松手触发 voice_talk_stop → record_loop 退出循环 →
             * 执行收尾（esp_codec_dev_close + send_text）。这条调用链
             * （含 esp_websocket_client_send_bin）在 4096 字节栈上会
             * 溢出，破坏栈窗口，表现为任务崩溃 → 连接被底层关闭。
             *
             * 与工程内其他任务（WS 客户端、play_task）统一为 8192。 */
            xTaskCreate(record_loop, "voice_rec", 8192, NULL, 5, &s_task);
        }
    } else if (strcmp(type, "asr_final") == 0) {
        if (json_get_str(json, "text", s_text_buf, sizeof(s_text_buf))) {
            ESP_LOGI(TAG, "识别: %s", s_text_buf);
        }
    } else if (strcmp(type, "llm_delta") == 0) {
        if (json_get_str(json, "text", s_text_buf, sizeof(s_text_buf))) {
            printf("BADGE_VOICE {\"delta\":\"%s\"}\n", s_text_buf);
        }
    } else if (strcmp(type, "llm_final") == 0) {
        /* 原来这里用栈上 char text[240]，装不下完整回复：
         * LLM_MAX_TOKENS=128 约 85 汉字，UTF-8 每字 3 字节 ≈ 255 字节，
         * 加标点与 emoji 更多。实测屏幕与串口都少了尾巴。
         *
         * 改用共享静态缓冲（见 s_text_buf 注释）：既够大，又不吃
         * WebSocket 事件任务那 4096 字节的栈。 */
        if (json_get_str(json, "text", s_text_buf, sizeof(s_text_buf))) {
            ESP_LOGI(TAG, "回复: %s", s_text_buf);
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
        /* 2026-11-23 修复：断连时必须把播放/录音相关的挂起状态一并清干净。
         *
         * 实测缺陷：板子收了完整音频（126 帧 / 497744 字节），但
         * BADGE_VOICE 卡在 state=PLAYING、BADGE_STATUS 显示 playing=false，
         * 之后心跳也发不出去，服务端 36s 后按心跳超时断开。
         *
         * 根因：原来这里只清 s_playing 就切到 ST_CONNECTING，但：
         *   1) s_play_draining / s_got_end_marker 仍是上一轮的值；
         *   2) 我加的收尾兜底判断的是 s_state == ST_PLAYING，
         *      而这里已把 s_state 改成 ST_CONNECTING —— 兜底永远不触发；
         *   3) play_task 若还在等数据，会一直卡在循环里。
         * 结果：状态机残留、播放任务残留，重连后行为错乱。
         *
         * 修法：断连时把这一轮的所有挂起标志复位，让重连从干净状态开始。 */
        s_play_draining = false;
        s_got_end_marker = false;
        s_end_grace_ticks = 0;
        s_talk_requested = false;
        s_talk_stop_requested = false;
        s_talk_pending_ticks = 0;
        if (s_play_mutex) xSemaphoreTake(s_play_mutex, portMAX_DELAY);
        s_play_head = s_play_tail = s_play_count = 0;
        if (s_play_mutex) xSemaphoreGive(s_play_mutex);
        if (s_spk) {
            esp_codec_dev_close(s_spk);
        }
        s_state = ST_CONNECTING; ui_sync();
        ESP_LOGW(TAG, "连接断开，已复位播放/录音挂起状态");
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
        /* 2026-11-23：记录发送结果。原来忽略返回值，心跳发不出去也毫无痕迹，
         * 只能从服务端「心跳超时」反推，排查成本很高。 */
        const esp_err_t hb_ret = send_text(buf);
        if (hb_ret != ESP_OK) {
            ESP_LOGW(TAG, "心跳发送失败 ret=%d connected=%d state=%d",
                     (int)hb_ret, (int)s_connected, (int)s_state);
        } else {
            ESP_LOGD(TAG, "心跳已发送 state=%d play_count=%u",
                     (int)s_state, (unsigned)s_play_count);
        }

        /* 2026-11-23 新增：录音请求在途超时保护。
         *
         * voice_talk_start 发出的 recording_start 依赖服务端回 session_start
         * 才真正启动采集。若服务端没回（网络抖动、turn_busy 被拒等），
         * s_talk_requested 会永久为 true，导致之后所有 TALK 都被新加的
         * 「在途」保护挡住 —— 用户表现为「怎么按都没反应」。
         *
         * 这里给一个 5 秒上限：超时就把在途标志清掉并复位状态，
         * 让用户可以直接重试，而不是必须重启板子。
         * 5 秒远大于正常往返（实测 session_start 在 200ms 内到达）。 */
        if (s_talk_requested && s_state == ST_RECORDING && !s_task) {
            if (++s_talk_pending_ticks >= 5) {   /* 5 × 1s */
                s_talk_pending_ticks = 0;
                s_talk_requested = false;
                s_talk_stop_requested = false;
                s_state = ST_IDLE;
                ui_sync();
                ESP_LOGW(TAG, "录音请求 5s 未收到 session_start，已放弃并复位");
            }
        } else {
            s_talk_pending_ticks = 0;
        }
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

    /* 启动时把当前音量同步到屏幕，避免状态行只显示"语音连接中"、
     * 看不出实际音量。 */
    badge_dashboard_volume(s_volume);

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
        /* 2026-11-23 修复：4096 太小，改为 8192。
         *
         * 实测现象：WebSocket 连接每 15~29 秒主动断开一次，
         * 导致录音被打断（chunks=0 → asr_empty）、音频帧传到一半丢失。
         *
         * 根因：esp_websocket_client 的任务栈原来只有 4096。
         * 本文件 record_loop 的注释里已经踩过同一个坑：
         *   "4096 字节栈不足以承载 esp_websocket_client_send_bin 的调用链，
         *    实测会在 record_loop 返回时（retw.n）因栈窗口损坏触发
         *    IllegalInstruction"
         * 那次只加大了 record_loop 的栈，**没有同步加大 WS 客户端自己的
         * task_stack** —— 收包/心跳/发送都在这个任务里跑，栈溢出会
         * 直接表现为连接被底层关闭。
         *
         * 8192 与工程内其他任务（record_loop、heartbeat）对齐。 */
        .task_stack = 8192,
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

    /* 心跳任务：只创建一次，内部按连接状态自行判断是否发送。
     *
     * 2026-11-23：优先级从 3 提到 5。
     * 原因：play_task 优先级 6 且原来写数据后不让出 CPU，会把它饿死，
     * 导致服务端 30s 收不到消息而断连。虽然 play_task 已改为写完
     * vTaskDelay(1) 让出，但心跳是连接存活的关键路径，不应排在
     * record_loop(5) 之后 —— 提到与 record_loop 同级，确保及时发出。 */
    static bool hb_started = false;
    if (!hb_started) {
        if (xTaskCreate(heartbeat_task, "voice_hb", 4096, NULL, 5, NULL) == pdPASS) {
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
        /* 播放任务同样涉及 codec 写入与重采样，4096 偏紧，加大到 8192。
         * 2026-11-23：注释一直这么写，但参数实际仍是 4096 —— 一并改对。
         * 播放任务里会调 esp_codec_dev_write（内部走 I2S 驱动栈），
         * 栈不足会在播放中途破坏上下文，表现为播放卡住或连接断开。 */
        if (xTaskCreate(play_task, "voice_play", 8192, NULL, 6, &s_play_task) != pdPASS) {
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

bool voice_is_started(void)
{
    return s_ws != NULL;
}

bool voice_playing_now(void)
{
    return s_playing;
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
    /* 2026-11-23 修复：防录音请求风暴。
     *
     * 实测缺陷：服务端日志出现同一 session 内 16 秒 9 次「开始录音」
     * （t1~t9），而「录音结束」只有 6 次、其中 4 次报 asr_empty。
     *
     * 成因：voice_talk_start 发出 recording_start 后立刻把 s_state 置为
     * ST_RECORDING，看似有保护；但 voice_talk_stop 在「按住时长不足
     * MIN_TALK_MS」时会**提前把 s_state 改回 ST_IDLE**（见下方最短时长
     * 保护分支），而此时服务端可能还没回 session_start、record_loop 也
     * 还没真正启动。用户手指抖动或连按就会：置 IDLE → 又能 start →
     * 又发一次 recording_start，但前一次的 turn 已在服务端建好，
     * 于是产生一串只发 session_start、没有音频的僵尸轮次。
     *
     * 修法：加一个「请求在途」标志，从发出 recording_start 起、
     * 到收到 session_start（或超时/取消）为止，拒绝再次 start。 */
    if (s_talk_requested) {
        ESP_LOGW(TAG, "TALK 忽略：上一次录音请求仍在途（等待 session_start）");
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

    /* 最短时长保护（2026-11-12 新增）。
     *
     * 问题：LVGL 的 LV_EVENT_LONG_PRESSED 在**按住满 300ms 时才触发**，
     * 也就是录音起点比手指按下晚 300ms。用户按 400ms 就松手的话，
     * 真正能录到的只有 100ms 左右，而服务端 ASR 要求 ≥0.2s，
     * 直接报 asr_failed: 音频过短，用户体感就是「说话没反应」。
     *
     * 串口实录（修复前）：
     *   I (12118) 长按说话 -> start=0
     *   I (12272) 松开发送              ← 只隔 154ms
     *   E (12310) asr_failed: 音频过短（0.080s < 0.2s）
     *
     * 处理：录到的时长不足 MIN_TALK_MS 时，本地取消本轮，不发 recording_stop。
     * 让服务端那轮自然超时/由 cancel 收尾，用户在屏幕和串口都能看到原因，
     * 而不是收到一个看不懂的服务端错误。 */
    const uint32_t elapsed_ms =
        (uint32_t)((esp_timer_get_time() - s_record_start_us) / 1000);
    if (s_record_start_us != 0 && elapsed_ms < MIN_TALK_MS) {
        ESP_LOGW(TAG, "录音仅 %ums（< %dms），已本地取消本轮。按住说话请久一点",
                 (unsigned)elapsed_ms, (int)MIN_TALK_MS);
        s_talk_stop_requested = true;
        s_talk_requested = false;
        /* 通知服务端作废本轮，避免它等 playback_done 白等到超时 */
        char cancel[JSON_BUF];
        snprintf(cancel, sizeof(cancel),
                 "{\"type\":\"cancel\",\"protocol_version\":%d,\"turn_id\":\"%s\"}",
                 PROTO_VERSION, s_turn_id);
        send_text(cancel);
        s_state = ST_IDLE;
        badge_dashboard_voice(2);
        return ESP_ERR_INVALID_SIZE;
    }

    s_talk_stop_requested = true;
    s_talk_requested = false;
    char buf[JSON_BUF];
    snprintf(buf, sizeof(buf),
             "{\"type\":\"recording_stop\",\"protocol_version\":%d}", PROTO_VERSION);
    send_text(buf);
    ESP_LOGI(TAG, "停止录音（已录 %ums）", (unsigned)elapsed_ms);
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
    /* 同步到屏幕（dashboard 的 refresh 定时器 100ms 内刷新） */
    badge_dashboard_volume(v);
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
