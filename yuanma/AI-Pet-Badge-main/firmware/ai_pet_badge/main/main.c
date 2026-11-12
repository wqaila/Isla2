#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "bsp/esp-bsp.h"
#include "driver/usb_serial_jtag.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "miniz.h"
#include "nvs_flash.h"
#include "badge_dashboard.h"
#include "badge_imu.h"
#include "badge_imu_filter.h"
#include "badge_motion.h"
#include "badge_scene.h"
#include "badge_character.h"
#include "badge_storage.h"
#include "voice_client.h"

static const char *TAG = "badge";
#ifdef BADGE_CHARACTER_QINGLAN
extern const uint8_t assets_start[] asm("_binary_qinglan_badge_start");
extern const uint8_t assets_end[] asm("_binary_qinglan_badge_end");
#else
extern const uint8_t assets_start[] asm("_binary_animations_badge_start");
extern const uint8_t assets_end[] asm("_binary_animations_badge_end");
#endif

typedef struct {
    char magic[8];
    uint32_t version, width, height, fps, clip_count, frame_count;
    uint32_t roi_x, roi_y, background_offset, background_length;
} pack_header_t;
typedef struct { char name[16]; uint32_t first, count; } pack_clip_t;
typedef struct { uint32_t offset, length; } pack_frame_t;
static const pack_header_t *pack;
static const pack_clip_t *clips;
static const pack_frame_t *frames;
static uint8_t *actor, *decoded, *transition_from;
static uint32_t idle_clip = UINT32_MAX;
static portMUX_TYPE request_mux = portMUX_INITIALIZER_UNLOCKED;
static uint32_t next_action_clip, requested_clip, clip_revision;
static bool motion_busy;
static int64_t benchmark_start;
static volatile uint32_t current_clip, current_frame, active_action, pending_clip = UINT32_MAX;
static volatile uint32_t completed_actions, late_frames, touch_count, displayed_frames;
static volatile uint32_t decode_time_us, render_time_us, peak_render_time_us;
static volatile badge_motion_stage_t current_stage;
static esp_codec_dev_handle_t speaker;

static esp_err_t check_assets(void)
{
    size_t length = assets_end-assets_start;
    if (length < sizeof(pack_header_t)) return ESP_ERR_INVALID_SIZE;
    pack = (const pack_header_t *)assets_start;
    if (memcmp(pack->magic,"BDGZ565\0",8) || pack->version != 2) {
        ESP_LOGE(TAG,"Asset format must be v2; rebuild using prepare_badge_assets.py");
        return ESP_ERR_INVALID_VERSION;
    }
    if (pack->width != BADGE_SPRITE_W || pack->height != BADGE_SPRITE_H ||
        pack->roi_x != 104 || pack->roi_y != 80 || pack->fps < 5 || pack->fps > 30 ||
        pack->clip_count != 3 || !pack->frame_count || pack->frame_count > 4096)
        return ESP_ERR_INVALID_ARG;
    size_t end = sizeof(*pack)+pack->clip_count*sizeof(*clips)+pack->frame_count*sizeof(*frames);
    if (end > length || pack->background_offset != end || !pack->background_length ||
        pack->background_length > length-end) return ESP_ERR_INVALID_SIZE;
    clips = (const pack_clip_t *)(assets_start+sizeof(*pack));
    frames = (const pack_frame_t *)(clips+pack->clip_count);
    const char *expected_clips[] = {BADGE_CHARACTER_CLIPS};
    for (unsigned i = 0; i < pack->clip_count; ++i) {
        if (!memchr(clips[i].name,0,sizeof(clips[i].name)) || clips[i].count < 2 ||
            clips[i].first >= pack->frame_count || clips[i].count > pack->frame_count-clips[i].first)
            return ESP_ERR_INVALID_SIZE;
        if (strcmp(clips[i].name,expected_clips[i])) return ESP_ERR_INVALID_ARG;
        if (!strcmp(clips[i].name,BADGE_CHARACTER_IDLE_CLIP)) idle_clip = i;
    }
    if (idle_clip == UINT32_MAX) return ESP_ERR_NOT_FOUND;
    size_t previous_end = end+pack->background_length;
    for (unsigned i = 0; i < pack->frame_count; ++i) {
        if (!frames[i].length || frames[i].offset != previous_end || frames[i].offset > length ||
            frames[i].length > length-frames[i].offset) return ESP_ERR_INVALID_SIZE;
        previous_end = frames[i].offset+frames[i].length;
    }
    if (previous_end != length) return ESP_ERR_INVALID_SIZE;
    ESP_LOGI(TAG,"Assets v2 validated: %u transparent frames, %u bytes",(unsigned)pack->frame_count,(unsigned)length);
    return ESP_OK;
}

static bool inflate(tinfl_decompressor *inflater, uint32_t offset, uint32_t length, uint8_t *out, size_t size)
{
    tinfl_init(inflater);
    size_t input = length, count = size;
    tinfl_status status = tinfl_decompress(inflater,assets_start+offset,&input,out,out,&count,
        TINFL_FLAG_PARSE_ZLIB_HEADER|TINFL_FLAG_USING_NON_WRAPPING_OUTPUT_BUF);
    return status == TINFL_STATUS_DONE && count == size && input == length;
}

static uint32_t request_next_action(void)
{
    portENTER_CRITICAL(&request_mux);
    uint32_t selected = BADGE_NO_ACTION;
    if (!motion_busy) {
        selected = requested_clip = next_action_clip;
        next_action_clip = (next_action_clip+1)%pack->clip_count;
        ++clip_revision;
    }
    portEXIT_CRITICAL(&request_mux);
    return selected;
}
/* 触摸说话：**按下即开始录音，松手发送**（2026-11-12 改造）
 *
 * 改造前用 LV_EVENT_LONG_PRESSED 作为录音起点，而该事件是在**按住满 300ms
 * 时**才触发一次，导致录音起点比手指按下晚 300ms。用户按 400ms 就松手，
 * 实际只录到约 100ms，服务端直接报 asr_failed: 音频过短。
 * 串口实录：长按说话 -> 松开发送 只隔 154ms，录到 0.080s。
 *
 * 现在改为 LV_EVENT_PRESSED（按下瞬间）开始录音，并用一个「短按」判定：
 * 松手时若总按住时长 < SHORT_PRESS_MS，则取消本轮录音并当作短按切动画。
 * 这样既保证了录音窗口 = 完整按住时长，又保住了短按切动画的功能。
 */
static uint32_t s_press_tick;        /* 按下的时刻（FreeRTOS tick） */
static bool     s_press_recording;   /* 本次按下是否已开始录音 */

#define SHORT_PRESS_MS  300          /* 低于此时长视为短按（切动画） */

static void on_voice_press(lv_event_t *event)
{
    (void)event;
    s_press_tick = xTaskGetTickCount();
    s_press_recording = false;

    if (!voice_connected()) {
        ESP_LOGW(TAG, "语音未连接服务端（发 VOICE <ip> 连接）");
        return;
    }
    esp_err_t r = voice_talk_start();
    s_press_recording = (r == ESP_OK);
    ESP_LOGI(TAG, "按下说话 -> start=%d", (int)r);
}

static void on_voice_release(lv_event_t *event)
{
    (void)event;
    const uint32_t held_ms =
        (uint32_t)((xTaskGetTickCount() - s_press_tick) * portTICK_PERIOD_MS);

    if (!s_press_recording) {
        /* 按下时没能开始录音（未连接等），只按短按处理 */
        if (held_ms < SHORT_PRESS_MS) {
            ++touch_count;
            uint32_t selected = request_next_action();
            if (selected != BADGE_NO_ACTION) {
                ESP_LOGI(TAG, "Touch -> queued %s", clips[selected].name);
            }
        }
        return;
    }

    s_press_recording = false;

    if (held_ms < SHORT_PRESS_MS) {
        /* 短按：取消录音，改为切动画。
         * voice_talk_stop 内部有最短时长保护，会本地取消并通知服务端。 */
        ESP_LOGI(TAG, "短按 %ums -> 取消录音，切动画", (unsigned)held_ms);
        voice_talk_stop();
        ++touch_count;
        uint32_t selected = request_next_action();
        if (selected != BADGE_NO_ACTION) {
            ESP_LOGI(TAG, "Touch -> queued %s", clips[selected].name);
        }
        return;
    }

    if (voice_talk_stop() == ESP_OK) {
        ESP_LOGI(TAG, "松开发送（按住 %ums）", (unsigned)held_ms);
    }
}
static void print_status(void)
{
    badge_imu_state_t imu; badge_imu_get(&imu);
    uint32_t queued = pending_clip;
    printf("BADGE_STATUS {\"character\":\"" BADGE_CHARACTER_ID "\",\"imu_mode\":\"shake_only\",\"orientation_enabled\":false,\"sd_mounted\":%s,\"standing_idle\":%s,\"clip\":\"%s\",\"playing\":%s,\"stage\":\"%s\",\"action\":\"%s\",\"pending\":\"%s\","
           "\"completed\":%u,\"late_frames\":%u,\"frame_index\":%u,\"frames\":%u,\"touches\":%u,"
           "\"decode_us\":%u,\"render_us\":%u,\"peak_render_us\":%u,\"heap\":%u,\"min_heap\":%u,\"psram\":%u,"
           "\"imu_available\":%s,\"angle\":%.2f,\"flat\":%s,\"shakes\":%u,\"dizzy\":%s}\n",
           badge_storage_mounted()?"true":"false",BADGE_CHARACTER_STANDING?"true":"false",clips[current_clip].name,current_stage != MOTION_IDLE?"true":"false",badge_motion_names[current_stage],
           clips[active_action].name,queued < pack->clip_count?clips[queued].name:"",
           (unsigned)completed_actions,(unsigned)late_frames,(unsigned)current_frame,(unsigned)displayed_frames,
           (unsigned)touch_count,(unsigned)decode_time_us,(unsigned)render_time_us,(unsigned)peak_render_time_us,
           (unsigned)esp_get_free_heap_size(),(unsigned)esp_get_minimum_free_heap_size(),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),imu.available?"true":"false",imu.angle,
           imu.flat?"true":"false",(unsigned)imu.shakes,current_stage >= MOTION_DIZZY_ENTER?"true":"false");
}
static void diagnostic_tone(void)
{
    if (!speaker) speaker = bsp_audio_codec_speaker_init();
    if (!speaker) { ESP_LOGE(TAG,"Speaker initialization failed"); return; }
    esp_codec_dev_sample_info_t format = {.sample_rate=16000,.channel=1,.bits_per_sample=16};
    if (esp_codec_dev_open(speaker,&format) != ESP_OK) return;
    esp_codec_dev_set_out_vol(speaker,35);
    int16_t samples[256];
    const float frequencies[] = {523.25f,659.25f,783.99f};
    for (int note=0; note<3; ++note) for (int block=0; block<10; ++block) {
        for (int i=0; i<256; ++i) {
            int sample=block*256+i;
            float envelope=fminf(1,fminf(sample/160.0f,(2560-sample)/160.0f));
            samples[i]=(int16_t)(3500*envelope*sinf(6.2831853f*frequencies[note]*sample/16000));
        }
        if (esp_codec_dev_write(speaker,samples,sizeof(samples)) != ESP_OK) {
            esp_codec_dev_close(speaker); return;
        }
    }
    esp_codec_dev_close(speaker);
}
/* ===== USB 带宽实测 =====
 *
 * 目的：验证 USB-Serial/JTAG 能否承载语音音频流。
 * 需求带宽：
 *   录音 16kHz / 4ch / 16bit = 128000 B/s
 *   播放 24kHz / 1ch / 16bit =  48000 B/s
 *   合计约 176000 B/s ≈ 1.4 Mbit/s
 *
 * 命令：USBW <秒数>
 * 行为：连续发 PCM 大小的二进制块（5120 字节 / 40ms），
 *       每秒打印一次统计。结束打印汇总。
 *
 * 注意：该测试与日志共用同一条 USB 通道，因此结果反映的是
 * "音频 + 日志" 混合场景下的真实可用带宽。
 */
static void usb_bandwidth_test(int seconds)
{
    if (seconds < 1) seconds = 1;
    if (seconds > 30) seconds = 30;

    /* 5120 字节 = 16kHz × 4ch × 16bit × 40ms，与真实录音块一致 */
    const size_t CHUNK = 5120;
    uint8_t *buf = heap_caps_malloc(CHUNK, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (!buf) {
        printf("BADGE_USBW {\"error\":\"no_mem\"}\n");
        return;
    }

    /* 填充可辨识的内容：4 字节序号 + 递增数据，
     * 便于主机端检测丢块与乱序。 */
    for (size_t i = 0; i < CHUNK; ++i) {
        buf[i] = (uint8_t)(i & 0xFF);
    }

    printf("BADGE_USBW {\"phase\":\"start\",\"seconds\":%d,\"chunk\":%u}\n",
           seconds, (unsigned)CHUNK);

    const int64_t t0 = esp_timer_get_time();
    const int64_t t_end = t0 + (int64_t)seconds * 1000000;
    uint32_t chunks = 0;
    uint32_t errors = 0;
    int64_t next_report = t0 + 1000000;
    int64_t next_send = t0;

    while (esp_timer_get_time() < t_end) {
        const int64_t now = esp_timer_get_time();

        /* 按 40ms 节拍发送，模拟真实音频间隔 */
        if (now < next_send) {
            vTaskDelay(1);
            continue;
        }
        next_send += 40000;

        /* 把序号写进前 4 字节，主机可据此检测丢块 */
        uint32_t seq = chunks;
        memcpy(buf, &seq, 4);

        const int written = usb_serial_jtag_write_bytes(buf, CHUNK, pdMS_TO_TICKS(500));
        if (written <= 0) {
            errors++;
        } else {
            chunks++;
        }

        if (now >= next_report) {
            next_report += 1000000;
            const float elapsed = (esp_timer_get_time() - t0) * 1e-6f;
            printf("BADGE_USBW {\"t\":%.1f,\"chunks\":%u,\"bytes\":%u,\"Bps\":%u}\n",
                   elapsed, (unsigned)chunks, (unsigned)(chunks * CHUNK),
                   (unsigned)(elapsed > 0 ? chunks * CHUNK / elapsed : 0));
        }
    }

    const float total = (esp_timer_get_time() - t0) * 1e-6f;
    printf("BADGE_USBW {\"phase\":\"done\",\"seconds\":%.2f,\"chunks\":%u,"
           "\"bytes\":%u,\"Bps\":%u,\"errors\":%u}\n",
           total, (unsigned)chunks, (unsigned)(chunks * CHUNK),
           (unsigned)(total > 0 ? chunks * CHUNK / total : 0),
           (unsigned)errors);
    free(buf);
}

static void command_task(void *arg)
{
    (void)arg;
    if (!usb_serial_jtag_is_driver_installed()) {
        usb_serial_jtag_driver_config_t config={.tx_buffer_size=1024,.rx_buffer_size=2048};
        if (usb_serial_jtag_driver_install(&config) != ESP_OK) { vTaskDelete(NULL); return; }
    }
    char line[64]; size_t used=0;
    while (true) {
        uint8_t byte;
        if (usb_serial_jtag_read_bytes(&byte,1,pdMS_TO_TICKS(250)) != 1) continue;
        if (byte=='\r') continue;
        if (byte=='\n') {
            line[used]=0;
            if (!strcmp(line,"STATUS")) print_status();
            else if (!strcmp(line,"NEXT")) { request_next_action(); print_status(); }
            else if (!strcmp(line,"TONE")) diagnostic_tone();
            else if (!strcmp(line,"DASH")) badge_dashboard_status();
            else if (!strcmp(line,"SHOT")) badge_dashboard_snapshot();
            else if (!strcmp(line,"IMU")) badge_imu_status();
            else if (!strcmp(line,"SD")) badge_storage_status();
            else if (!strcmp(line,"IMU CALIBRATE")) badge_imu_calibrate();
            else if (!strcmp(line,"IMU BENCH")) {
                portENTER_CRITICAL(&request_mux); benchmark_start=esp_timer_get_time(); portEXIT_CRITICAL(&request_mux);
                printf("BADGE_BENCHMARK started: ten seconds of dizzy effects; fixed screen\n");
            }
            else if (!strcmp(line,"IMU SELFTEST")) {
                int failures=badge_imu_filter_selftest()+badge_motion_selftest()+badge_scene_selftest();
                printf("BADGE_SELFTEST {\"failures\":%d}\n",failures);
            } else if (!strncmp(line,"USBW",4)) {
                int secs = 5;
                if (line[5]) secs = atoi(line + 5);
                usb_bandwidth_test(secs);
            } else if (!strcmp(line,"VOL")) {
                printf("BADGE_VOICE {\"volume\":%d}\n", voice_get_volume());
            } else if (!strncmp(line,"VOL ",4)) {
                voice_set_volume(atoi(line + 4));
            } else if (!strncmp(line,"VOICE",5)) {
                if (!strncmp(line,"VOICE STOP",10)) {
                    voice_stop();
                    printf("BADGE_VOICE {\"stopped\":true}\n");
                } else if (!strncmp(line,"VOICE STATUS",12)) {
                    voice_print_status();
                } else {
                    /* VOICE <ip> [port]：连服务端 */
                    char ip[64] = {0};
                    int port = 8765;
                    int got = sscanf(line + 5, "%63s %d", ip, &port);
                    if (got >= 1 && ip[0]) {
                        esp_err_t r = voice_start(ip, port);
                        printf("BADGE_VOICE {\"start\":\"%s\",\"ok\":%s}\n",
                               ip, r == ESP_OK ? "true" : "false");
                    } else {
                        printf("BADGE_ERROR usage: VOICE <ip> [port]\n");
                    }
                }
            } else if (!strncmp(line,"TALK",4)) {
                if (!strncmp(line,"TALK STOP",9)) {
                    voice_talk_stop();
                } else {
                    esp_err_t r = voice_talk_start();
                    printf("BADGE_VOICE {\"talk\":%s}\n",
                           r == ESP_OK ? "true" : "false");
                }
            } else if (!strncmp(line,"TIME ",5)) {
                badge_dashboard_set_time((time_t)strtoll(line+5,NULL,10)); badge_dashboard_status();
            } else if (used) printf("BADGE_ERROR unknown command\n");
            used=0;
        } else if (byte>=32 && byte<127) {
            if (used<sizeof(line)-1) line[used++]=byte;
            else used=0;
        }
    }
}

static void animation_task(void *arg)
{
    (void)arg;
    tinfl_decompressor *inflater=heap_caps_malloc(sizeof(*inflater),MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
    assert(inflater);
    uint32_t counts[3]={clips[0].count,clips[1].count,clips[2].count};
    badge_motion_t motion; badge_motion_init(&motion,idle_clip,counts,pack->fps);
    motion.standing_idle = BADGE_CHARACTER_STANDING;
    uint32_t revision=0, seen_shakes=0, decoded_key=UINT32_MAX, actor_key=UINT32_MAX, blend_epoch=0;
    bool initial=true, was_blending=false;
    int64_t active_benchmark=0;
    uint32_t benchmark_frames=0;
    int64_t deadline=esp_timer_get_time();
    while (true) {
        int64_t now=esp_timer_get_time();
        uint32_t request=BADGE_NO_ACTION;
        int64_t benchmark;
        portENTER_CRITICAL(&request_mux);
        if (revision != clip_revision) { request=requested_clip; revision=clip_revision; }
        benchmark=benchmark_start;
        portEXIT_CRITICAL(&request_mux);
        if (benchmark && benchmark != active_benchmark) {
            active_benchmark=benchmark; benchmark_frames=displayed_frames;
        }
        if (benchmark && now-benchmark >= 10000000) {
            float elapsed=(now-benchmark)*0.000001f;
            printf("BADGE_BENCHMARK {\"seconds\":%.3f,\"frames\":%u,\"fps\":%.2f}\n",
                   elapsed,(unsigned)(displayed_frames-benchmark_frames),(displayed_frames-benchmark_frames)/elapsed);
            badge_scene_timings();
            portENTER_CRITICAL(&request_mux);
            if (benchmark_start==benchmark) benchmark_start=0;
            portEXIT_CRITICAL(&request_mux);
            benchmark=active_benchmark=0;
        }
        badge_imu_state_t imu; badge_imu_get(&imu);
        bool shake=seen_shakes != imu.shakes;
        seen_shakes=imu.shakes;
        badge_motion_stage_t old_stage=motion.stage;
        badge_motion_tick(&motion,now,request,shake);
        bool dizzy=badge_motion_dizzy(&motion), blending=badge_motion_blending(&motion);
        portENTER_CRITICAL(&request_mux); motion_busy=dizzy; portEXIT_CRITICAL(&request_mux);
        badge_imu_inhibit(dizzy);
        badge_dashboard_motion(motion.stage != MOTION_IDLE);
        badge_dashboard_pending(motion.pending != BADGE_NO_ACTION);
        badge_dashboard_dizzy(dizzy,motion.stage == MOTION_RECOVER);
        if (old_stage != motion.stage) ESP_LOGI(TAG,"Motion: %s",badge_motion_names[motion.stage]);
        if (motion.blend_epoch != blend_epoch) {
            memcpy(transition_from,actor,BADGE_SPRITE_BYTES); blend_epoch=motion.blend_epoch;
        }
        uint32_t key=clips[motion.clip].first+motion.frame;
        bool pose_changed=initial || key != actor_key || blending || was_blending;
        int64_t started=esp_timer_get_time();
        decode_time_us=0;
        if (pose_changed && key != decoded_key) {
            const pack_frame_t *frame=&frames[key];
            if (!inflate(inflater,frame->offset,frame->length,decoded,BADGE_SPRITE_BYTES)) {
                ESP_LOGE(TAG,"Sprite decompression failed at %u",(unsigned)key);
                vTaskDelay(pdMS_TO_TICKS(1000)); continue;
            }
            decoded_key=key; decode_time_us=esp_timer_get_time()-started;
        }
        if (blending) {
            badge_scene_blend(actor,transition_from,decoded,badge_motion_blend(&motion,now));
            actor_key=UINT32_MAX;
        } else if (pose_changed) {
            /* Scene rendering copies the actor into its own buffers, so raw
             * pose buffers can exchange ownership without a 120 KiB copy. */
            uint8_t *previous=actor; actor=decoded; decoded=previous;
            decoded_key=actor_key; actor_key=key;
        }
        float strength=badge_motion_dizzy_strength(&motion,now);
        float phase=(now-motion.dizzy_us)*0.000001f;
        float dx=0,dy=0,lean=0;
        if (dizzy) { lean+=6*sinf(phase*9)*strength; dx+=1.5f*sinf(phase*7)*strength; }
        const float orientation=0;
        if (benchmark) {
            phase=(now-benchmark)*0.000001f;
            strength=1; lean=6*sinf(phase*9); dx=1.5f*sinf(phase*7);
        }
        if (badge_scene_present(actor,pose_changed,dx,dy,lean,orientation,strength,phase)) ++displayed_frames;
        render_time_us=esp_timer_get_time()-started;
        if (render_time_us>peak_render_time_us) peak_render_time_us=render_time_us;
        current_clip=motion.clip; current_frame=motion.frame; current_stage=motion.stage;
        active_action=motion.active; pending_clip=motion.pending; completed_actions=motion.completed;
        initial=false; was_blending=blending;
        deadline+=1000000/pack->fps;
        now=esp_timer_get_time();
        if (deadline>now) vTaskDelay(pdMS_TO_TICKS((deadline-now)/1000) ?: 1);
        else { ++late_frames; deadline=now; vTaskDelay(1); }
    }
}

void app_main(void)
{
    esp_err_t result=nvs_flash_init();
    if (result == ESP_ERR_NVS_NO_FREE_PAGES || result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase()); result=nvs_flash_init();
    }
    ESP_ERROR_CHECK(result); ESP_ERROR_CHECK(check_assets());
    requested_clip=current_clip=active_action=idle_clip;
    current_frame=clips[idle_clip].count-1;
    actor=heap_caps_calloc(1,BADGE_SPRITE_BYTES,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    decoded=heap_caps_malloc(BADGE_SPRITE_BYTES,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    transition_from=heap_caps_malloc(BADGE_SPRITE_BYTES,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    uint8_t *background=heap_caps_malloc(BADGE_FRAME_BYTES,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    tinfl_decompressor *inflater=heap_caps_malloc(sizeof(*inflater),MALLOC_CAP_8BIT);
    assert(actor && decoded && transition_from && background && inflater);
    assert(inflate(inflater,pack->background_offset,pack->background_length,background,BADGE_FRAME_BYTES));
    free(inflater);
    bsp_display_cfg_t display_config = {
        .lv_adapter_cfg=ESP_LV_ADAPTER_DEFAULT_CONFIG(), .rotation=ESP_LV_ADAPTER_ROTATE_0,
        .tear_avoid_mode=ESP_LV_ADAPTER_TEAR_AVOID_MODE_NONE, .touch_flags={0}
    };
    display_config.lv_adapter_cfg.task_core_id=0;
    display_config.lv_adapter_cfg.task_priority=4;
    assert(bsp_display_start_with_config(&display_config)); ESP_ERROR_CHECK(bsp_display_brightness_set(75));
    bsp_display_lock(-1);
    lv_obj_t *screen=lv_screen_active();
    lv_obj_set_style_bg_color(screen,lv_color_hex(0xFFF0F5),0);
    lv_obj_remove_flag(screen,LV_OBJ_FLAG_SCROLLABLE);
    /* 触摸交互（2026-11-12 改造）：
     *   PRESSED  按下瞬间 -> 开始录音（不再等 300ms 长按阈值）
     *   RELEASED 松手     -> 停止录音；若按住不足 SHORT_PRESS_MS 则取消录音并切动画
     *
     * 不再注册 CLICKED / LONG_PRESSED：
     *   - CLICKED 会在 release 之后额外触发一次，与上面的短按判定重复切动画
     *   - LONG_PRESSED 在按住满 300ms 才触发，正是原来录音窗口少 300ms 的根因
     */
    lv_obj_add_event_cb(screen, on_voice_press,   LV_EVENT_PRESSED,  NULL);
    lv_obj_add_event_cb(screen, on_voice_release, LV_EVENT_RELEASED, NULL);
    badge_scene_init(background);
    bsp_display_unlock();
    badge_dashboard_start(); badge_imu_start();
    badge_storage_start();
    assert(xTaskCreatePinnedToCore(animation_task,"animation",8192,NULL,4,NULL,1)==pdPASS);
    assert(xTaskCreate(command_task,"commands",12288,NULL,2,NULL)==pdPASS);
    badge_network_start();
    ESP_LOGI(TAG,"%s IMU dashboard ready; commands: STATUS, NEXT, DASH, SHOT, IMU, IMU CALIBRATE, IMU SELFTEST",BADGE_CHARACTER_ID);
}
