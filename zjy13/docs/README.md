# zjy13 · 技术文档索引

本目录共 28 篇文档，按用途分类如下。项目总览见 [`../README.md`](../README.md)。

> 另有 1 篇不在本目录、但同属技术文档：[`../asr/README.md`](../asr/README.md)
> （本地 ASR 的环境、安装与命令行用法），列在文末「同项目其他文档」。

## 🗓 工作记录

| 文档 | 内容 |
|---|---|
| [`WORKLOG-2026-11-12.md`](WORKLOG-2026-11-12.md) | **最新**：延迟优化（流式 TTS，首块 5106→1685ms）+ 音量 60→85 + 固件烧录 |
| [`SUMMARY-2026-11-09.md`](SUMMARY-2026-11-09.md) | **总览**：今日 12 项修复的成果、不足与遗留，含后续优先级建议 |
| [`WORKLOG-2026-11-09.md`](WORKLOG-2026-11-09.md) | 盘查后修复 12 个问题（多轮上下文、播放看门狗、2ch 降混、wetext 文本前端、依赖与配置模板、心跳与连接上限、语速生效、一键启动）+ CI |
| [`WORKLOG-2026-11-04.md`](WORKLOG-2026-11-04.md) | 无线直连改造、语音链路稳定性、24kHz 音质修复；含 7 个 Bug 与 7 条弯路 |
| [`WORKLOG-2026-11-02.md`](WORKLOG-2026-11-02.md) | 仓库同步、固件崩溃与分片缺陷修复、字库缺字、音量与长按交互 |
| [`development-log.md`](development-log.md) | 开发全记录（模块清单、关键决策、已知问题）。正文截至 2026-10-28，已追加「后续进展」章节 |
| [`PROJECT-SUMMARY.md`](PROJECT-SUMMARY.md) | 完成情况总览、踩坑与修复、当前阻塞。正文截至 2026-10-29，第六/七/十节已按现状改写 |

> 两份文档都已标注 2026-11-04 更新：补充了后续进展、订正了已过时的「未验证项 / 当前阻塞 / 下一步」。
> 10-30 之后的逐日细节仍以两份 `WORKLOG` 为准。

## 🐞 故障与审查

| 文档 | 内容 |
|---|---|
| [`BOARD-DEFECTS-2026-11-12.md`](BOARD-DEFECTS-2026-11-12.md) | **最新**：板子与固件缺陷排查（长按录音失效、无打断、无唤醒词、无 OTA） |
| [`FAULT-blackout-and-playback.md`](FAULT-blackout-and-playback.md) | 故障报告：黑屏与播放中断。**5.0 节记录已证实的三个真实根因** |
| [`character-review.md`](character-review.md) | 角色卡审查：已修复 3 个缺陷 + 6 个待决策设计缺陷 |
| [`board-firmware-audit.md`](board-firmware-audit.md) | 板子与现有固件的不足审查 |
| [`firmware-flash-audit-plan.md`](firmware-flash-audit-plan.md) | 烧录前检查方案与问题清单（烧录已完成的记录见 8 节） |

## 🔌 协议与固件

| 文档 | 内容 |
|---|---|
| [`protocol.md`](protocol.md) | WebSocket 通信协议（消息类型、时序、状态机、错误码） |
| [`firmware.md`](firmware.md) | 固件说明（职责边界、数据流、两套串口命令、编译烧录） |
| [`hardware-verification.md`](hardware-verification.md) | 1.85B 硬件资料核实（音频引脚、I2S 格式、4 声道采集） |
| [`esp32-board-report.md`](esp32-board-report.md) | 板子状况报告与固件备份 |

## ✅ 分阶段验收

| 文档 | 内容 |
|---|---|
| [`asr-plan.md`](asr-plan.md) | 阶段一：本地 ASR 方案 |
| [`asr-report.md`](asr-report.md) | 阶段一：本地 ASR 验收（RTF 0.009） |
| [`stage3-dialogue-loop.md`](stage3-dialogue-loop.md) | 阶段三：终端语音对话闭环 |
| [`tts-report.md`](tts-report.md) | 本地 TTS 验收报告（CosyVoice 3.0） |
| [`tts-performance.md`](tts-performance.md) | CosyVoice3 性能优化实测（含 `token_hop_len` 污染 Bug） |
| [`model-selection.md`](model-selection.md) | DeepSeek 模型选型报告 |

## 🎨 音色与环境

| 文档 | 内容 |
|---|---|
| [`voice-elysia.md`](voice-elysia.md) | 角色音色克隆：素材筛选、`<|endofprompt|>` 前缀坑 |
| [`v2-direction.md`](v2-direction.md) | V2 方案转向分析（本地 TTS 版） |
| [`wsl2-cosyvoice-guide.md`](wsl2-cosyvoice-guide.md) | WSL2 验证与 CosyVoice 部署指南 |
| [`wsl2-gpu-verified.md`](wsl2-gpu-verified.md) | WSL2 GPU 验证结果与网络配置 |

## 📖 操作指南

| 文档 | 内容 |
|---|---|
| [`demo-guide.md`](demo-guide.md) | 演示指南：**方式零 = 板子语音对话（主力）**，另有网页/终端/报告三种纯软件方式 |

## 🔗 同项目其他文档（不在本目录）

| 文档 | 内容 |
|---|---|
| [`../asr/README.md`](../asr/README.md) | 本地 ASR 环境要求（cu128 torch）、安装、命令行用法、输出字段与故障排查 |
| [`../README.md`](../README.md) | 项目总览：架构、实测数据、配置依据、未验证项与下一步 |
