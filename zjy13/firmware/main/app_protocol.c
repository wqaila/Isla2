/* 协议实现：二进制帧编解码 + 轻量 JSON 构造/解析。 */

#include "app_protocol.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "app_board.h"
#include "app_config.h"
#include "esp_log.h"

static const char *TAG = "app_proto";

/* -------------------------------------------------------------------
 * CRC32（与服务端 zlib.crc32 计算结果一致）
 *
 * 服务端：zlib.crc32(turn_id.encode("utf-8")) & 0xFFFFFFFF
 * 这里用标准 CRC-32（IEEE 802.3，多项式 0xEDB88320），
 * 与 zlib 的实现完全相同。
 * ------------------------------------------------------------------- */

uint32_t app_proto_turn_short(const char *turn_id)
{
    if (turn_id == NULL) {
        return 0;
    }

    uint32_t crc = 0xFFFFFFFFu;
    for (const unsigned char *p = (const unsigned char *)turn_id; *p; ++p) {
        crc ^= (uint32_t)(*p);
        for (int i = 0; i < 8; ++i) {
            crc = (crc >> 1) ^ (0xEDB88320u & ((crc & 1u) ? 0xFFFFFFFFu : 0u));
        }
    }
    return crc ^ 0xFFFFFFFFu;
}

/* -------------------------------------------------------------------
 * 音频帧编解码
 * ------------------------------------------------------------------- */

esp_err_t app_proto_encode_audio(uint16_t seq, uint32_t turn_short,
                                 const void *payload, size_t payload_len,
                                 uint8_t *out, size_t out_cap, size_t *out_len)
{
    if (out == NULL || out_len == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (payload_len > APP_MAX_AUDIO_CHUNK) {
        ESP_LOGE(TAG, "音频块过大: %u > %d", (unsigned)payload_len,
                 APP_MAX_AUDIO_CHUNK);
        return ESP_ERR_INVALID_SIZE;
    }
    const size_t total = APP_BINARY_HEADER_SIZE + payload_len;
    if (out_cap < total) {
        return ESP_ERR_NO_MEM;
    }

    /* 手写小端序列化：不依赖编译器对 packed 结构的字节序假设 */
    out[0] = APP_BINARY_MAGIC;
    out[1] = APP_BIN_TYPE_AUDIO_UP;
    out[2] = (uint8_t)(seq & 0xFF);
    out[3] = (uint8_t)((seq >> 8) & 0xFF);
    out[4] = (uint8_t)(turn_short & 0xFF);
    out[5] = (uint8_t)((turn_short >> 8) & 0xFF);
    out[6] = (uint8_t)((turn_short >> 16) & 0xFF);
    out[7] = (uint8_t)((turn_short >> 24) & 0xFF);
    out[8] = (uint8_t)(payload_len & 0xFF);
    out[9] = (uint8_t)((payload_len >> 8) & 0xFF);
    out[10] = (uint8_t)((payload_len >> 16) & 0xFF);
    out[11] = (uint8_t)((payload_len >> 24) & 0xFF);

    if (payload_len > 0) {
        if (payload == NULL) {
            return ESP_ERR_INVALID_ARG;
        }
        memcpy(out + APP_BINARY_HEADER_SIZE, payload, payload_len);
    }
    *out_len = total;
    return ESP_OK;
}

esp_err_t app_proto_decode_audio(const uint8_t *data, size_t len,
                                 app_bin_header_t *hdr)
{
    if (data == NULL || hdr == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (len < APP_BINARY_HEADER_SIZE) {
        return ESP_ERR_INVALID_SIZE;
    }
    if (data[0] != APP_BINARY_MAGIC) {
        ESP_LOGW(TAG, "二进制帧魔数不匹配: 0x%02X", data[0]);
        return ESP_ERR_INVALID_CRC;
    }

    hdr->magic = data[0];
    hdr->msg_type = data[1];
    hdr->seq = (uint16_t)(data[2] | (data[3] << 8));
    hdr->turn_short = ((uint32_t)data[4]) | ((uint32_t)data[5] << 8) |
                      ((uint32_t)data[6] << 16) | ((uint32_t)data[7] << 24);
    hdr->payload_len = ((uint32_t)data[8]) | ((uint32_t)data[9] << 8) |
                       ((uint32_t)data[10] << 16) | ((uint32_t)data[11] << 24);

    if (hdr->msg_type != APP_BIN_TYPE_AUDIO_UP &&
        hdr->msg_type != APP_BIN_TYPE_AUDIO_DOWN) {
        ESP_LOGW(TAG, "未知二进制类型: 0x%02X", hdr->msg_type);
        return ESP_ERR_INVALID_ARG;
    }
    if (hdr->payload_len > APP_MAX_AUDIO_CHUNK) {
        ESP_LOGW(TAG, "负载声明过大: %u", (unsigned)hdr->payload_len);
        return ESP_ERR_INVALID_SIZE;
    }
    if (len - APP_BINARY_HEADER_SIZE < hdr->payload_len) {
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

/* -------------------------------------------------------------------
 * JSON 构造
 *
 * 服务端 envelope 必带 type + protocol_version。
 * session_id / turn_id / seq 由服务端出站时补，客户端上行不需要。
 * ------------------------------------------------------------------- */

int app_proto_build_hello(char *buf, size_t cap, const char *device_id,
                          const char *firmware, const char *token)
{
    if (buf == NULL || cap == 0) {
        return -1;
    }
    const char *tok = (token == NULL) ? "" : token;
    const int n = snprintf(
        buf, cap,
        "{\"type\":\"%s\",\"protocol_version\":%d,"
        "\"device_id\":\"%s\",\"firmware\":\"%s\",\"token\":\"%s\"}",
        APP_MSG_DEVICE_HELLO, APP_PROTOCOL_VERSION,
        device_id == NULL ? "" : device_id,
        firmware == NULL ? "" : firmware, tok);
    return (n > 0 && (size_t)n < cap) ? n : -1;
}

int app_proto_build_recording_start(char *buf, size_t cap, int sample_rate,
                                    int channels, int bits,
                                    const char *encoding,
                                    const char *layout)
{
    if (buf == NULL || cap == 0) {
        return -1;
    }
    const int n = snprintf(
        buf, cap,
        "{\"type\":\"%s\",\"protocol_version\":%d,"
        "\"sample_rate\":%d,\"channels\":%d,\"bits\":%d,"
        "\"encoding\":\"%s\",\"channel_layout\":\"%s\"}",
        APP_MSG_RECORDING_START, APP_PROTOCOL_VERSION,
        sample_rate, channels, bits,
        encoding == NULL ? "pcm_s16le" : encoding,
        layout == NULL ? APP_MIC_LAYOUT : layout);
    return (n > 0 && (size_t)n < cap) ? n : -1;
}

int app_proto_build_recording_stop(char *buf, size_t cap)
{
    if (buf == NULL || cap == 0) {
        return -1;
    }
    const int n = snprintf(
        buf, cap, "{\"type\":\"%s\",\"protocol_version\":%d}",
        APP_MSG_RECORDING_STOP, APP_PROTOCOL_VERSION);
    return (n > 0 && (size_t)n < cap) ? n : -1;
}

int app_proto_build_playback_done(char *buf, size_t cap,
                                  const char *turn_id,
                                  uint32_t received_bytes,
                                  uint32_t received_frames)
{
    if (buf == NULL || cap == 0) {
        return -1;
    }

    /* 有 turn_id 就带上，服务端据此核对轮次；
     * 没有则省略该字段（服务端会按当前轮处理）。 */
    int n;
    if (turn_id != NULL && turn_id[0] != '\0') {
        n = snprintf(
            buf, cap,
            "{\"type\":\"%s\",\"protocol_version\":%d,"
            "\"turn_id\":\"%s\",\"received_bytes\":%u,"
            "\"received_frames\":%u}",
            APP_MSG_PLAYBACK_DONE, APP_PROTOCOL_VERSION, turn_id,
            (unsigned)received_bytes, (unsigned)received_frames);
    } else {
        n = snprintf(
            buf, cap,
            "{\"type\":\"%s\",\"protocol_version\":%d,"
            "\"received_bytes\":%u,\"received_frames\":%u}",
            APP_MSG_PLAYBACK_DONE, APP_PROTOCOL_VERSION,
            (unsigned)received_bytes, (unsigned)received_frames);
    }
    return (n > 0 && (size_t)n < cap) ? n : -1;
}

int app_proto_build_heartbeat(char *buf, size_t cap)
{
    if (buf == NULL || cap == 0) {
        return -1;
    }
    const int n = snprintf(
        buf, cap, "{\"type\":\"%s\",\"protocol_version\":%d}",
        APP_MSG_HEARTBEAT, APP_PROTOCOL_VERSION);
    return (n > 0 && (size_t)n < cap) ? n : -1;
}

int app_proto_build_cancel(char *buf, size_t cap, const char *turn_id)
{
    if (buf == NULL || cap == 0) {
        return -1;
    }
    int n;
    if (turn_id != NULL && turn_id[0] != '\0') {
        n = snprintf(
            buf, cap,
            "{\"type\":\"%s\",\"protocol_version\":%d,\"turn_id\":\"%s\"}",
            APP_MSG_CANCEL, APP_PROTOCOL_VERSION, turn_id);
    } else {
        n = snprintf(
            buf, cap, "{\"type\":\"%s\",\"protocol_version\":%d}",
            APP_MSG_CANCEL, APP_PROTOCOL_VERSION);
    }
    return (n > 0 && (size_t)n < cap) ? n : -1;
}

/* -------------------------------------------------------------------
 * 轻量 JSON 解析
 *
 * 只处理扁平结构，够用即可。不引入 cJSON 是为了省 flash（约 20KB）
 * 和解析时的堆分配 —— 嵌入式上频繁 malloc 容易碎片化。
 * ------------------------------------------------------------------- */

/**
 * 在 json 里定位 "key"，返回冒号后的起点（已跳过空白）。
 *
 * 关键点：必须确认匹配的是**完整的键**，
 * 否则找 "turn_id" 会误命中 "turn_id_short"。
 */
static const char *find_value_start(const char *json, const char *key)
{
    if (json == NULL || key == NULL) {
        return NULL;
    }
    const size_t klen = strlen(key);
    const char *p = json;

    while ((p = strchr(p, '"')) != NULL) {
        ++p;  /* 跳过起始引号 */
        if (strncmp(p, key, klen) != 0) {
            continue;
        }
        /* 键必须正好结束（后面紧跟引号），避免前缀误匹配 */
        if (p[klen] != '"') {
            continue;
        }

        const char *q = p + klen + 1;  /* 越过结束引号 */
        while (*q == ' ' || *q == '\t' || *q == '\n' || *q == '\r') {
            ++q;
        }
        if (*q != ':') {
            continue;
        }
        ++q;
        while (*q == ' ' || *q == '\t' || *q == '\n' || *q == '\r') {
            ++q;
        }
        return q;
    }
    return NULL;
}

bool app_json_get_raw(const char *json, const char *key,
                      const char **out, size_t *out_len)
{
    const char *v = find_value_start(json, key);
    if (v == NULL || *v != '"') {
        return false;
    }
    ++v;
    const char *end = v;
    /* 找到未转义的结束引号 */
    while (*end && !(*end == '"' && end[-1] != '\\')) {
        ++end;
    }
    if (*end != '"') {
        return false;
    }
    *out = v;
    *out_len = (size_t)(end - v);
    return true;
}

bool app_json_get_string(const char *json, const char *key,
                         char *out, size_t out_cap)
{
    if (out == NULL || out_cap == 0) {
        return false;
    }
    const char *v;
    size_t n;
    if (!app_json_get_raw(json, key, &v, &n)) {
        return false;
    }

    size_t o = 0;
    for (size_t i = 0; i < n && o + 1 < out_cap; ++i) {
        char c = v[i];
        if (c == '\\' && i + 1 < n) {
            char esc = v[++i];
            switch (esc) {
            case '"':  c = '"';  break;
            case '\\': c = '\\'; break;
            case '/':  c = '/';  break;
            case 'n':  c = '\n'; break;
            case 't':  c = '\t'; break;
            case 'r':  c = '\r'; break;
            case 'b':  c = '\b'; break;
            case 'f':  c = '\f'; break;
            case 'u':
                /* \uXXXX：本项目的中文文本由服务端直接输出 UTF-8，
                 * 不走 \u 转义。这里保守处理为问号，避免产生乱码。 */
                if (i + 4 < n) {
                    i += 4;
                }
                c = '?';
                break;
            default:   c = esc;  break;
            }
        }
        out[o++] = c;
    }
    out[o] = '\0';
    return true;
}

bool app_json_get_int(const char *json, const char *key, int32_t *out)
{
    if (out == NULL) {
        return false;
    }
    const char *v = find_value_start(json, key);
    if (v == NULL) {
        return false;
    }
    /* 支持 true/false 转 1/0，方便复用 */
    if (strncmp(v, "true", 4) == 0) {
        *out = 1;
        return true;
    }
    if (strncmp(v, "false", 5) == 0) {
        *out = 0;
        return true;
    }
    if (*v == '"') {
        ++v;
    }
    char *end = NULL;
    const long val = strtol(v, &end, 10);
    if (end == v) {
        return false;
    }
    *out = (int32_t)val;
    return true;
}

bool app_json_get_string_raw_object(const char *json, const char *key,
                                    char *out, size_t out_cap)
{
    if (json == NULL || key == NULL || out == NULL || out_cap == 0) {
        return false;
    }
    const char *v = find_value_start(json, key);
    if (v == NULL || *v != '{') {
        return false;
    }

    /* 从 '{' 开始按括号深度扫描到匹配的 '}'，跳过字符串内的括号 */
    int depth = 0;
    bool in_str = false;
    const char *p = v;
    const char *begin = v + 1;
    const char *end = NULL;

    for (; *p; ++p) {
        char c = *p;
        if (in_str) {
            if (c == '\\' && p[1]) {
                ++p;          /* 跳过转义字符 */
            } else if (c == '"') {
                in_str = false;
            }
            continue;
        }
        if (c == '"') {
            in_str = true;
        } else if (c == '{') {
            depth++;
        } else if (c == '}') {
            depth--;
            if (depth == 0) {
                end = p;
                break;
            }
        }
    }
    if (end == NULL) {
        return false;
    }

    const size_t len = (size_t)(end - begin);
    if (len + 1 > out_cap) {
        return false;
    }
    memcpy(out, begin, len);
    out[len] = '\0';
    return true;
}

bool app_json_get_bool(const char *json, const char *key, bool *out)
{
    if (out == NULL) {
        return false;
    }
    int32_t v = 0;
    if (app_json_get_int(json, key, &v)) {
        *out = (v != 0);
        return true;
    }
    return false;
}
