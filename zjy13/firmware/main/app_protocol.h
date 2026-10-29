#pragma once

/*
 * WebSocket 消息协议（固件侧）
 *
 * 与服务端 server/protocol/messages.py 严格对齐。
 * 任何一方改动都必须同步另一侧，否则联调必失败。
 *
 * 消息分两类：
 *   1. 控制消息 —— JSON 文本帧
 *   2. 音频数据 —— 二进制帧，12 字节头 + PCM
 *
 * 为什么音频不用 JSON：
 *   16kHz 4ch 16bit 1 秒 = 128KB，Base64 后 171KB，再加 JSON 转义与解析开销。
 *   二进制帧没有这些成本，ESP32 侧解析也简单。
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

/* ===== 二进制帧头 =====
 *
 * 小端，固定 12 字节：
 *   偏移 长 字段
 *   0    1  magic = 0xA5
 *   1    1  msg_type（0x01 上行 / 0x02 下行）
 *   2    2  seq（uint16，回绕）
 *   4    4  turn_id_short（uint32，CRC32 of turn_id）
 *   8    4  payload_len（uint32）
 *   12   N  payload
 */
typedef struct __attribute__((packed)) {
    uint8_t  magic;
    uint8_t  msg_type;
    uint16_t seq;
    uint32_t turn_short;
    uint32_t payload_len;
} app_bin_header_t;

_Static_assert(sizeof(app_bin_header_t) == 12, "二进制帧头必须是 12 字节");

/* ===== 消息类型（与 messages.py 的常量逐字一致）===== */
#define APP_MSG_DEVICE_HELLO     "device_hello"
#define APP_MSG_SERVER_HELLO     "server_hello"
#define APP_MSG_SESSION_START    "session_start"
#define APP_MSG_RECORDING_START  "recording_start"
#define APP_MSG_RECORDING_STOP   "recording_stop"
#define APP_MSG_ASR_PARTIAL      "asr_partial"
#define APP_MSG_ASR_FINAL        "asr_final"
#define APP_MSG_LLM_START        "llm_start"
#define APP_MSG_LLM_DELTA        "llm_delta"
#define APP_MSG_LLM_FINAL        "llm_final"
#define APP_MSG_TTS_START        "tts_start"
#define APP_MSG_PLAYBACK_START   "playback_start"
#define APP_MSG_PLAYBACK_DONE    "playback_done"
#define APP_MSG_TURN_DONE        "turn_done"
#define APP_MSG_CANCEL           "cancel"
#define APP_MSG_TURN_CANCELLED   "turn_cancelled"
#define APP_MSG_ERROR            "error"
#define APP_MSG_HEARTBEAT        "heartbeat"
#define APP_MSG_HEARTBEAT_ACK    "heartbeat_ack"

/* ===== 错误码 ===== */
#define APP_ERR_PROTOCOL_VERSION_MISMATCH "protocol_version_mismatch"
#define APP_ERR_BAD_MESSAGE               "bad_message"
#define APP_ERR_NOT_HELLO                 "not_hello"
#define APP_ERR_UNKNOWN_TYPE              "unknown_type"
#define APP_ERR_TURN_BUSY                 "turn_busy"
#define APP_ERR_AUDIO_TOO_LARGE           "audio_too_large"
#define APP_ERR_AUDIO_BUFFER_OVERFLOW     "audio_buffer_overflow"
#define APP_ERR_TEXT_TOO_LONG             "text_too_long"
#define APP_ERR_ASR_FAILED                "asr_failed"
#define APP_ERR_ASR_EMPTY                 "asr_empty"
#define APP_ERR_LLM_FAILED                "llm_failed"
#define APP_ERR_TTS_FAILED                "tts_failed"
#define APP_ERR_TTS_UNAVAILABLE           "tts_unavailable"
#define APP_ERR_CANCELLED                 "cancelled"
#define APP_ERR_INTERNAL                  "internal"

/* ===== 帧编解码 ===== */

/**
 * @brief 编码一个上行音频帧。
 *
 * @param seq          块序号
 * @param turn_short   turn_id 的 CRC32
 * @param payload      PCM 数据
 * @param payload_len  负载长度
 * @param[out] out     输出缓冲，需至少 12 + payload_len 字节
 * @param out_cap      输出缓冲容量
 * @param[out] out_len 实际写入长度
 *
 * @return APP_OK / APP_ERR_INVALID_ARG / APP_ERR_NO_MEM
 */
esp_err_t app_proto_encode_audio(uint16_t seq, uint32_t turn_short,
                                 const void *payload, size_t payload_len,
                                 uint8_t *out, size_t out_cap, size_t *out_len);

/**
 * @brief 解析一个下行音频帧头，并校验完整性。
 *
 * @param data     收到的二进制数据
 * @param len      数据长度
 * @param[out] hdr 解析出的帧头
 *
 * @return APP_OK / APP_ERR_PROTOCOL / APP_ERR_INCOMPLETE
 */
esp_err_t app_proto_decode_audio(const uint8_t *data, size_t len,
                                 app_bin_header_t *hdr);

/**
 * @brief 计算 turn_id 的 CRC32（与服务端 turn_id_to_short 一致）。
 *
 * 服务端用 zlib.crc32，这里必须得到相同结果，否则二进制帧匹配不上。
 */
uint32_t app_proto_turn_short(const char *turn_id);

/* ===== JSON 构造辅助 =====
 *
 * 不引入 cJSON 依赖（能省 flash 和内存），
 * 手写格式化足够，且格式完全可控。
 * 返回值：成功写入的字节数；< 0 表示缓冲不足。
 */

/**
 * @brief 构造 device_hello。
 *
 * @param token 访问令牌，可为 NULL 或空串
 */
int app_proto_build_hello(char *buf, size_t cap, const char *device_id,
                          const char *firmware, const char *token);

/**
 * @brief 构造 recording_start（告知本次录音格式）。
 */
int app_proto_build_recording_start(char *buf, size_t cap, int sample_rate,
                                    int channels, int bits,
                                    const char *encoding,
                                    const char *layout);

/** @brief 构造 recording_stop。 */
int app_proto_build_recording_stop(char *buf, size_t cap);

/** @brief 构造 playback_done，带上实际收到的字节数与帧数供服务端校验。 */
int app_proto_build_playback_done(char *buf, size_t cap,
                                  const char *turn_id,
                                  uint32_t received_bytes,
                                  uint32_t received_frames);

/** @brief 构造 heartbeat。 */
int app_proto_build_heartbeat(char *buf, size_t cap);

/** @brief 构造 cancel（带 turn_id，可为 NULL）。 */
int app_proto_build_cancel(char *buf, size_t cap, const char *turn_id);

/**
 * @brief 从 JSON 文本里取一个字符串字段。
 *
 * 轻量解析：只处理本项目需要的扁平字段，不实现通用 JSON 解析器。
 * 处理转义（\" \\ \/ \n \t \uXXXX 简化处理）。
 *
 * @return true 找到
 */
bool app_json_get_string(const char *json, const char *key,
                         char *out, size_t out_cap);

/** @brief 取整数或浮点字段（浮点按四舍五入取整）。 */
bool app_json_get_int(const char *json, const char *key, int32_t *out);

/**
 * @brief 取出一个嵌套 JSON 对象（不含外层大括号）到 out。
 *
 * 本协议的 envelope 是平铺的，但 audio_in / audio_out 仍是嵌套对象。
 * 需要先取出子对象文本，再在子对象里取字段，避免误读到同名顶层字段。
 *
 * @return true 找到且长度足够
 */
bool app_json_get_string_raw_object(const char *json, const char *key,
                                    char *out, size_t out_cap);

/** @brief 取布尔字段。 */
bool app_json_get_bool(const char *json, const char *key, bool *out);

/** @brief 取字符串字段的指针与长度（用于长文本，避免复制）。 */
bool app_json_get_raw(const char *json, const char *key,
                      const char **out, size_t *out_len);
