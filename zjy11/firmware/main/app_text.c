#include "app_text.h"

#include <string.h>

/* 判断某字节是否为 UTF-8 续字节（10xxxxxx）。 */
static bool is_cont(unsigned char c)
{
    return (c & 0xC0) == 0x80;
}

size_t app_utf8_length(const char *text)
{
    if (text == NULL) {
        return 0;
    }
    return strlen(text);
}

size_t app_utf8_chars(const char *text)
{
    if (text == NULL) {
        return 0;
    }
    size_t chars = 0;
    for (const unsigned char *p = (const unsigned char *)text; *p != '\0'; ++p) {
        if (!is_cont(*p)) {
            ++chars;
        }
    }
    return chars;
}

bool app_utf8_fits(const char *text, size_t max_bytes)
{
    if (text == NULL) {
        return true;
    }
    return app_utf8_length(text) <= max_bytes;
}

size_t app_utf8_truncate(const char *text, size_t max_chars,
                         char *out, size_t out_size)
{
    if (out == NULL || out_size == 0) {
        return 0;
    }
    out[0] = '\0';
    if (text == NULL || max_chars == 0) {
        return 0;
    }

    size_t chars = 0;
    size_t used = 0;
    const unsigned char *p = (const unsigned char *)text;

    while (*p != '\0') {
        /* 先量出当前字符的字节长度 */
        size_t len = 1;
        if ((*p & 0xE0) == 0xC0) {
            len = 2;
        } else if ((*p & 0xF0) == 0xE0) {
            len = 3;
        } else if ((*p & 0xF8) == 0xF0) {
            len = 4;
        }

        /* 序列被截断或非法时停止，避免写入半个字符 */
        for (size_t i = 1; i < len; ++i) {
            if (p[i] == '\0' || !is_cont(p[i])) {
                out[used] = '\0';
                return used;
            }
        }
        if (chars >= max_chars || used + len + 1 > out_size) {
            break;
        }
        memcpy(out + used, p, len);
        used += len;
        chars += 1;
        p += len;
    }

    out[used] = '\0';
    return used;
}

size_t app_text_plain(const char *text, char *out, size_t out_size)
{
    if (out == NULL || out_size == 0) {
        return 0;
    }
    out[0] = '\0';
    if (text == NULL) {
        return 0;
    }

    size_t used = 0;
    bool at_line_start = true;
    bool last_was_blank = false;

    for (const char *p = text; *p != '\0'; ++p) {
        const char c = *p;

        /* 行首的 Markdown 标记直接丢弃 */
        if (at_line_start) {
            if (c == '#' || c == '*' || c == '-' || c == '+' || c == '>') {
                continue;
            }
            if (c == ' ' || c == '\t') {
                continue;
            }
            at_line_start = false;
        }

        /* 行内的强调与代码标记丢弃 */
        if (c == '`') {
            continue;
        }
        if (c == '*') {
            continue;
        }

        if (c == '\n' || c == '\r') {
            if (last_was_blank) {
                continue;
            }
            if (used + 2 > out_size) {
                break;
            }
            out[used++] = '\n';
            at_line_start = true;
            last_was_blank = true;
            continue;
        }

        last_was_blank = false;
        if (used + 1 >= out_size) {
            break;
        }
        out[used++] = c;
    }

    /* 去掉结尾多余换行 */
    while (used > 0 && (out[used - 1] == '\n' || out[used - 1] == ' ')) {
        --used;
    }
    out[used] = '\0';
    return used;
}
