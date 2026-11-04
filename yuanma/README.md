# yuanma —— 原厂固件源码（板子上实际运行的固件）

本目录存放**板子出厂配套工程 `AI-Pet-Badge-main` 的源码**。

## 为什么在这里

`zjy13` 是把这块 ESP32-S3 开发板改造成语音对话设备的主项目，但它自己的
`zjy13/firmware`（工程名 `zjy13_voice`）只是一个精简的**协议验证固件**。

板子上**实际烧录并运行**的，是这里的 `AI-Pet-Badge-main/firmware/ai_pet_badge`
（约 8.7MB），它自带：

- 屏幕角色动画（精灵图 + 混合过渡）
- 仪表盘（时间、日期、天气、语音状态、音量）
- IMU 摇晃互动
- SD 卡存储
- **语音客户端**（`main/voice_client.c`，与 `zjy13` 的服务端对接）

语音链路的所有实测与缺陷修复都发生在这个工程里。

## 目录结构

```
yuanma/
├── AI-Pet-Badge-main/          # 上游工程（自带 README）
│   ├── firmware/               # ESP-IDF 固件工程
│   │   └── ai_pet_badge/       # ← 实际烧录的工程
│   ├── backend/                # 上游的后端（本项目未使用）
│   ├── docs/                   # 上游文档
│   ├── tests/ tools/ artifacts/
│   └── README.md               # ← 上游原始说明，从这里开始看
├── jump.mp4 / walk.mp4 / Sit.mp4   # 角色动画源视频（用于生成精灵图素材）
```

## 与 zjy13 的关系

```
yuanma/.../ai_pet_badge  ──WebSocket──►  zjy13/server (ASR + LLM + TTS)
        （板子固件）                          （电脑侧服务）
```

- **协议**：共用 `zjy13/docs/protocol.md`
- **固件侧改动与踩坑**：见 [`../zjy13/docs/WORKLOG-2026-11-02.md`](../zjy13/docs/WORKLOG-2026-11-02.md)、[`../zjy13/docs/WORKLOG-2026-11-04.md`](../zjy13/docs/WORKLOG-2026-11-04.md)
- **故障根因分析**：见 [`../zjy13/docs/FAULT-blackout-and-playback.md`](../zjy13/docs/FAULT-blackout-and-playback.md)

## 编译与烧录

```powershell
$env:IDF_TOOLS_PATH='D:\esp\.espressif'
. 'D:\esp\esp-idf-5.5.5\export.ps1'
cd yuanma\AI-Pet-Badge-main\firmware\ai_pet_badge
idf.py build

python -m esptool --chip esp32s3 -p COM7 -b 921600 write_flash `
  --flash_mode dio --flash_freq 80m --flash_size 16MB 0x20000 build\ai_pet_badge.bin
```

## 注意

- `firmware/ai_pet_badge/main/wifi_config.h` 是**本地私有配置**（Wi-Fi 密码 + 服务端地址），
  已被根 `.gitignore` 排除，**不入库**
- `AI-Pet-Badge-main/.env.wifi` 同样含 Wi-Fi 凭据，注意不要提交
- 固件 `build/`、`managed_components/` 均为产物，不入库
