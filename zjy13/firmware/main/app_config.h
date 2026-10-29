#pragma once

/*
 * 固件运行配置。
 *
 * 密钥/地址不硬编码：Wi-Fi 密码与服务端地址在编译期通过
 * 菜单配置（Kconfig.projbuild）或 -D 宏传入，
 * 也可以用运行时 NVS 覆盖（见 app_config.c）。
 */

/* ===== 服务端 ===== */
#if defined(CONFIG_APP_SERVER_HOST)
#define APP_SERVER_HOST   CONFIG_APP_SERVER_HOST
#elif defined(APP_SERVER_HOST_OVERRIDE)
#define APP_SERVER_HOST   APP_SERVER_HOST_OVERRIDE
#else
#define APP_SERVER_HOST   "192.168.1.100"
#endif

#if defined(CONFIG_APP_SERVER_PORT)
#define APP_SERVER_PORT   CONFIG_APP_SERVER_PORT
#elif defined(APP_SERVER_PORT_OVERRIDE)
#define APP_SERVER_PORT   APP_SERVER_PORT_OVERRIDE
#else
#define APP_SERVER_PORT   8765
#endif

/* 设备访问令牌（服务端以 --token 启动时必填，否则留空） */
#if defined(CONFIG_APP_DEVICE_TOKEN)
#define APP_DEVICE_TOKEN  CONFIG_APP_DEVICE_TOKEN
#else
#define APP_DEVICE_TOKEN  ""
#endif

/* 设备标识，服务端日志里用于区分多台设备 */
#if defined(CONFIG_APP_DEVICE_ID)
#define APP_DEVICE_ID     CONFIG_APP_DEVICE_ID
#else
#define APP_DEVICE_ID     "esp32s3-01"
#endif

/* ===== Wi-Fi =====
 *
 * 优先用 Kconfig 配置；没配则回落到 NVS 里保存的凭据。
 * 两处都没有则无法连网（固件会明确报错，不会静默卡住）。
 */
#if defined(CONFIG_APP_WIFI_SSID)
#define APP_WIFI_SSID     CONFIG_APP_WIFI_SSID
#else
#define APP_WIFI_SSID     ""
#endif

#if defined(CONFIG_APP_WIFI_PASSWORD)
#define APP_WIFI_PASSWORD CONFIG_APP_WIFI_PASSWORD
#else
#define APP_WIFI_PASSWORD ""
#endif

/* ===== 协议常量（必须与服务端 server/protocol/messages.py 一致）===== */

#define APP_PROTOCOL_VERSION   1
#define APP_BINARY_MAGIC       0xA5
#define APP_BINARY_HEADER_SIZE 12

#define APP_BIN_TYPE_AUDIO_UP    0x01   /* 板子 → 电脑：录音 */
#define APP_BIN_TYPE_AUDIO_DOWN  0x02   /* 电脑 → 板子：TTS */

/* 单条 JSON 上限 64KB */
#define APP_MAX_JSON_BYTES     (64 * 1024)

/* 单个音频块上限 48KB（协议规定）。本固件实际按 40ms 分块，约 5KB。 */
#define APP_MAX_AUDIO_CHUNK    (48 * 1024)

/* 单轮音频总量上限 60 秒 */
#define APP_MAX_TURN_AUDIO_SEC 60

/* 单轮文本上限 1000 字 */
#define APP_MAX_TEXT_CHARS     1000

/* ===== 时序 ===== */

/* 心跳间隔 20 秒（协议规定） */
#define APP_HEARTBEAT_INTERVAL_MS  20000

/* 心跳超时 30 秒无消息则判定断线 */
#define APP_HEARTBEAT_TIMEOUT_MS  30000

/* 单轮超时 60 秒 */
#define APP_TURN_TIMEOUT_MS       60000

/* Wi-Fi 连接超时 */
#define APP_WIFI_TIMEOUT_MS       20000

/* WebSocket 重连退避：1s, 2s, 4s, 8s, 8s... */
#define APP_WS_RECONNECT_MIN_MS   1000
#define APP_WS_RECONNECT_MAX_MS   8000

/* ===== 缓冲 ===== */

/* 下行音频接收缓冲（服务端单轮最多约 60 秒 24kHz 单声道 = 2.88MB）
 * 但 ESP32 内存装不下整段，所以边收边播（流式）。
 * 这里只保留一个抖动缓冲。 */
#define APP_PLAYBACK_RING_MS      200

/* 上行录音缓冲 */
#define APP_RECORD_RING_MS        200
