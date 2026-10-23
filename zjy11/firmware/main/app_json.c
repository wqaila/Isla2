#include "app_json.h"

#include <stdio.h>
#include <string.h>

#include "app_text.h"

/* 复制并转义一个 JSON 字符串到 out。返回写入字节数，0 表示空间不足。 */
static size_t json_escape(const char *text, char *out, size_t out_size)
{
    if (out == NULL || out_size == 0) {
        return 0;
    }
    size_t used = 0;
    for (const unsigned char *p = (const unsigned char *)text; *p != '\0'; ++p) {
        const char *rep = NULL;
        char buf[8];
        switch (*p) {
        case '"':  rep = "\\\""; break;
        case '\\': rep = "\\\\"; break;
        case '\n': rep = "\\n";  break;
        case '\r': rep = "\\r";  break;
        case '\t': rep = "\\t";  break;
        default: break;
        }
        if (rep != NULL) {
            const size_t n = strlen(rep);
            if (used + n + 1 > out_size) {
                return 0;
            }
            memcpy(out + used, rep, n);
            used += n;
        } else if (*p < 0x20) {
            /* 其余控制字符用 \u00XX，避免破坏 JSON */
            const int n = snprintf(buf, sizeof(buf), "\\u%04x", *p);
            if (n <= 0 || used + (size_t)n + 1 > out_size) {
                return 0;
            }
            memcpy(out + used, buf, (size_t)n);
            used += (size_t)n;
        } else {
            if (used + 2 > out_size) {
                return 0;
            }
            out[used++] = (char)*p;
        }
    }
    out[used] = '\0';
    return used;
}

size_t app_json_escaped_size(const char *text)
{
    if (text == NULL) {
        return 1;
    }
    size_t need = 1;
    for (const unsigned char *p = (const unsigned char *)text; *p != '\0'; ++p) {
        switch (*p) {
        case '"':
        case '\\': need += 2; break;
        case '\n': need += 2; break;
        case '\r': need += 2; break;
        case '\t': need += 2; break;
        default:
            need += (*p < 0x20) ? 6 : 1;
            break;
        }
    }
    return need;
}

size_t app_build_chat_request(const char *request_id,
                              const char *session_id,
                              const char *message,
                              char *out, size_t out_size)
{
    if (out == NULL || out_size == 0 || request_id == NULL || message == NULL) {
        return 0;
    }
    out[0] = '\0';

    /* 消息必须非空且不超过网关的字节上限 */
    if (!app_utf8_fits(message, APP_MAX_MESSAGE_BYTES) || app_utf8_length(message) == 0) {
        return 0;
    }

    char esc_msg[APP_MAX_MESSAGE_BYTES * 2 + 8];
    if (json_escape(message, esc_msg, sizeof(esc_msg)) == 0) {
        return 0;
    }

    int written;
    if (session_id == NULL || session_id[0] == '\0') {
        written = snprintf(out, out_size,
                           "{\"request_id\":\"%s\",\"session_id\":null,\"message\":\"%s\"}",
                           request_id, esc_msg);
    } else {
        char esc_sid[APP_SESSION_ID_CHARS * 2 + 8];
        if (json_escape(session_id, esc_sid, sizeof(esc_sid)) == 0) {
            return 0;
        }
        written = snprintf(out, out_size,
                           "{\"request_id\":\"%s\",\"session_id\":\"%s\",\"message\":\"%s\"}",
                           request_id, esc_sid, esc_msg);
    }

    if (written <= 0 || (size_t)written >= out_size ||
        (size_t)written > APP_MAX_REQUEST_BYTES) {
        out[0] = '\0';
        return 0;
    }
    return (size_t)written;
}

/*
 * 从 body 中查找 "key":"value" 的字符串值并复制到 out。
 * 只处理平坦字符串，足够覆盖本项目的响应结构。
 */
static bool json_find_string(const char *body, const char *key,
                             char *out, size_t out_size)
{
    char needle[64];
    const int n = snprintf(needle, sizeof(needle), "\"%s\"", key);
    if (n <= 0) {
        return false;
    }

    const char *p = strstr(body, needle);
    if (p == NULL) {
        return false;
    }
    p += (size_t)n;

    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') {
        ++p;
    }
    if (*p != ':') {
        return false;
    }
    ++p;
    while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') {
        ++p;
    }
    if (*p != '"') {
        return false;
    }
    ++p;

    size_t used = 0;
    while (*p != '\0' && *p != '"') {
        if (*p == '\\') {
            ++p;
            if (*p == '\0') {
                return false;
            }
            char decoded = *p;
            switch (*p) {
            case 'n': decoded = '\n'; break;
            case 'r': decoded = '\r'; break;
            case 't': decoded = '\t'; break;
            default:  decoded = *p;  break;
            }
            if (used + 2 > out_size) {
                return false;
            }
            out[used++] = decoded;
            ++p;
            continue;
        }
        if (used + 2 > out_size) {
            return false;
        }
        out[used++] = *p++;
    }
    if (*p != '"') {
        return false;
    }
    out[used] = '\0';
    return true;
}

bool app_parse_chat_reply(const char *body, size_t len, app_reply_t *reply)
{
    if (body == NULL || reply == NULL) {
        return false;
    }
    (void)len;  /* 后续换用流式解析时需要；当前按 NUL 结尾处理 */

    memset(reply, 0, sizeof(*reply));

    if (!json_find_string(body, "reply", reply->reply, sizeof(reply->reply))) {
        return false;
    }
    reply->reply_len = app_utf8_length(reply->reply);
    if (reply->reply_len == 0) {
        return false;
    }

    (void)json_find_string(body, "session_id", reply->session_id,
                           sizeof(reply->session_id));

    const size_t clean = app_text_plain(reply->reply, reply->reply,
                                        sizeof(reply->reply));
    reply->reply_len = clean;
    return clean > 0;
}

app_error_t app_parse_error_body(const char *body, size_t len)
{
    if (body == NULL) {
        return APP_ERROR_CLOUD_FAILED;
    }
    (void)len;

    char code[64];
    if (!json_find_string(body, "code", code, sizeof(code))) {
        return APP_ERROR_CLOUD_FAILED;
    }
    return app_map_error_code(code);
}
