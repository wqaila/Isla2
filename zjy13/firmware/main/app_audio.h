#pragma once

/* I2S 音频采集与播放。 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

/**
 * @brief 初始化音频（I2S 全双工 + ES7210 ADC + ES8311 DAC）。
 *
 * 注意：ES7210 与 ES8311 共用 I2S 总线（全双工模式），
 * 且两者的时钟必须一致 —— 采集 16k、播放 24k 无法在同一条 I2S 上同时工作。
 *
 * 因此本固件采用**半双工分时**策略：
 *   - 录音时配置 I2S 为 16kHz，只开 RX
 *   - 播放时配置 I2S 为 24kHz，只开 TX
 * 每次切换都要重新配置并复位编解码器。
 *
 * 这不只是技术限制，也是产品要求：板载麦克风与扬声器距离很近，
 * 全双工会产生啸叫（V2 第九节要求第一版必须半双工）。
 *
 * @return ESP_OK 成功
 */
esp_err_t app_audio_init(void);

/* ===== 录音 ===== */

/** @brief 开始录音（切到 RX 模式，重新配置 I2S 为 16kHz）。 */
esp_err_t app_audio_record_start(void);

/** @brief 停止录音。 */
esp_err_t app_audio_record_stop(void);

/**
 * @brief 读取一块录音数据。
 *
 * 4 通道交错 int16。调用方负责分帧上传。
 *
 * @param buf      输出缓冲
 * @param cap      缓冲容量（字节）
 * @param[out] got 实际读到的字节数
 * @param timeout_ms 超时
 */
esp_err_t app_audio_record_read(void *buf, size_t cap, size_t *got,
                                uint32_t timeout_ms);

/* ===== 播放 ===== */

/** @brief 开始播放（切到 TX 模式，重新配置 I2S 为 24kHz，开功放）。 */
esp_err_t app_audio_play_start(void);

/** @brief 停止播放（关功放）。 */
esp_err_t app_audio_play_stop(void);

/**
 * @brief 写入一块待播放的 PCM（单声道 24kHz int16）。
 *
 * @param data       PCM 数据
 * @param len        字节数
 * @param written    实际写入的字节数（可为 NULL）
 * @param timeout_ms 超时
 */
esp_err_t app_audio_play_write(const void *data, size_t len,
                               size_t *written, uint32_t timeout_ms);

/**
 * @brief 等待播放缓冲排空。
 *
 * 用于 playback_done 上报前确保音频真的播完了，
 * 而不是"数据写进去了"就当播完。
 */
esp_err_t app_audio_play_drain(uint32_t timeout_ms);

/** @brief 当前是否在录音。 */
bool app_audio_recording(void);

/** @brief 当前是否在播放。 */
bool app_audio_playing(void);
