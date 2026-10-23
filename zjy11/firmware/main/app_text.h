#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/*
 * UTF-8 文本工具。
 *
 * 中文在 UTF-8 下通常占 3 字节，所以「字数」和「字节数」必须分开：
 * 网关按字节校验（512），屏幕排版按字符计算宽度。
 */

/* 统计 UTF-8 字节数（不含结尾 0）。非法序列按字节计，不崩溃。 */
size_t app_utf8_length(const char *text);

/* 统计字符数（码点）。非 ASCII 连续字节按一个字符计。 */
size_t app_utf8_chars(const char *text);

/* 是否在字节限制内（不含结尾 0）。 */
bool app_utf8_fits(const char *text, size_t max_bytes);

/*
 * 按「不超过 max_chars 个字符」截断到 out，保证不切断 UTF-8 多字节序列。
 * 返回实际写入的字节数。
 */
size_t app_utf8_truncate(const char *text, size_t max_chars,
                         char *out, size_t out_size);

/*
 * 把模型回答整理成小屏可读的纯文本：
 *   1. 去掉 Markdown 标记（#、*、`、列表符号）
 *   2. 合并多余空行
 *   3. 保留中文标点与换行
 * 输出写入 out 并返回长度。
 */
size_t app_text_plain(const char *text, char *out, size_t out_size);
