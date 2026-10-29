#pragma once

/*
 * 语音客户端：WebSocket 上传录音、接收 TTS 音频并播放。
 *
 * 协议与电脑侧 server/protocol/messages.py 严格一致：
 *   控制消息 JSON 文本帧，音频 12 字节头 + PCM 二进制帧。
 *
 * 板子不做任何推理，只负责收发音频与控制消息。
 *
 * 重要约束（来自硬件与 BSP 实测）：
 *   1. ES7210 与 ES8311 共用一条 I2S，采样率必须一致，因此
 *      录音与播放都用 22050（BSP 默认值），服务端负责重采样。
 *   2. 半双工：录音时关功放，播放时停止录音。
 *
 * 命令（USB 串口）：
 *   VOICE            连接并开始待命
 *   VOICE STOP       断开
 *   VOICE STATUS     打印状态
 *   TALK             开始录音（松手用 TALK STOP）
 *   TALK STOP        停止录音并上传
 */

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

/** @brief 启动语音客户端（连服务端）。host 为空则用编译期配置。 */
esp_err_t voice_start(const char *host, int port);

/** @brief 停止并断开。 */
void voice_stop(void);

/** @brief 是否已连接服务端。 */
bool voice_connected(void);

/** @brief 开始录音（上传音频）。 */
esp_err_t voice_talk_start(void);

/** @brief 停止录音并发送 recording_stop。 */
esp_err_t voice_talk_stop(void);

/** @brief 打印当前状态（供串口命令使用）。 */
void voice_print_status(void);
