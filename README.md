<div align="center">

# 赛博玩具 AI · 次元萌盒

**一个围绕「二次元 AI 陪伴」展开的个人项目集合**

从产品方案一路做到可运行的本地 LLM 聊天系统、角色语音克隆，再到 ESP32 硬件端语音交互

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![ESP-IDF](https://img.shields.io/badge/ESP--IDF-5.5-E7352C?logo=espressif&logoColor=white)
![Android](https://img.shields.io/badge/Android-Kotlin-3DDC84?logo=kotlin&logoColor=white)
![CUDA](https://img.shields.io/badge/CUDA-12.8-76B900?logo=nvidia&logoColor=white)
![Projects](https://img.shields.io/badge/子项目-11_个-blue)

</div>

---

## 📖 这是什么

这是一份持续了近两个月的个人开发记录。目标很具体：**让一个动漫角色活在一块小屏幕上** —— 能听、能说、有声音、有性格。

围绕着这个目标拆出了 11 个子项目，各自解决一个环节：

```
想法/方案  →  本地大模型  →  角色人设  →  声音克隆  →  硬件端交互
 zjy2/zjy12    zjy6/zjy7/zjy8    zjy9      zjy10/zjy13      zjy11/zjy13
```

---

## 🗂 项目一览

| # | 项目 | 定位 | 技术栈 | 状态 |
|:-:|---|---|---|:-:|
| 1 | [`zjy2`](zjy2/) | **次元萌盒** · 桌面级 AI 硬件产品方案 | 产品文档 | 📝 方案 |
| 2 | [`zjy5`](zjy5/) | **B站下载器 + 视频台词识别** | Python / yt-dlp / Playwright / Whisper / PaddleOCR | ✅ 可用 |
| 3 | [`zjy6`](zjy6/) | **Qwen 多端本地部署** | Android(Kotlin+JNI) / llama.cpp | ⚠️ 部分 |
| 4 | [`zjy7`](zjy7/) | **爱莉希雅 LoRA 微调** | transformers / peft / Qwen2.5-7B | ✅ 流水线完整 |
| 5 | [`zjy8`](zjy8/) | **PC 本地推理 + 手机远程调用** | Python / llama-server / Ollama | ✅ 可用 |
| 6 | [`zjy9`](zjy9/) | **爱莉希雅 AI 聊天系统**（主力） | FastAPI + Android + 树莓派 + Web | ✅ 功能最完整 |
| 7 | [`zjy10`](zjy10/) | **Anime Voice Lab** · 角色声线复刻 | 文档（规划）/ GPT-SoVITS | 📝 规划 |
| 8 | [`zjy11`](zjy11/) | **ESP32-S3 云端角色对话原型** | Python 网关 / ESP-IDF | ⚠️ 网关可用 |
| 9 | [`zjy12`](zjy12/) | **设计决策台账 + LLM 基准** | 文档 / 标准库 bench | ✅ 文档+基准 |
| 10 | [`zjy13`](zjy13/) | **ESP32-S3 语音交互系统**（最新，板子已烧录联调通过） | FunASR + DeepSeek + CosyVoice3 + ESP-IDF | ✅ 可用 |
| 11 | [`yuanma`](yuanma/) | **原厂固件源码**（板子上实际运行的 `ai_pet_badge`） | ESP-IDF 5.5.5 / LVGL / ES7210+ES8311 | ✅ 可编译 |

状态图例：✅ 可运行 · ⚠️ 部分可用 · 📝 仅文档 · 🚧 开发中

---

## ⭐ 重点看这两个

### zjy13 —— 完整语音交互系统（当前主力）

**架构**：本地 ASR + 云端 LLM + 本地 TTS，板子只做采集与播放

```
┌──────────────┐   Wi-Fi/WebSocket   ┌────────────────────────────┐
│  ESP32-S3    │◄───────────────────►│  电脑                        │
│  录音/显示/播放 │    JSON + PCM 二进制 │  ASR  FunASR（GPU）         │
└──────────────┘                     │  LLM  DeepSeek 云端 API     │
                                     │  TTS  CosyVoice3（WSL2/GPU）│
                                     └────────────────────────────┘
```

**已实测跑通的指标**（全部为真实执行记录）：

| 环节 | 实测结果 |
|---|---|
| ASR 识别 | RTF **0.009**（比实时快 111 倍） |
| LLM 首字延迟 | **646 ms**（P95 984ms，deepseek-flash） |
| TTS 合成 | RTF **0.62~0.70**（CosyVoice3-0.5B） |
| 端到端 | 约 **4~5 秒** |
| 显存并发 | ASR+TTS 同时驻留 **7286 / 12227 MiB**，5 轮无泄漏 |
| 单元测试 | `.venv` **262 passed** / `.venv-asr` **272 passed** |
| WebSocket | 录音上传 170 块 / 599760 字节，**乱序 0 重复 0** |

**角色与音色**：

- 角色卡：爱莉希雅（《崩坏3》真我之律者），提示词 1816 字
- 音色克隆：从 21 分钟语音素材自动选段（92 候选按 SNR 打分），选定 9.70s / SNR 60.8dB
- 参考文本经 **ASR 回读校验**，与音频逐字一致

<details>
<summary><b>🔧 踩过的关键坑（点击展开）</b></summary>

| 问题 | 根因 | 修复 |
|---|---|---|
| CosyVoice 音色注册成功但合成必崩 | 参考文本缺 `endofprompt` 前缀，LLM 前缀错位 | 服务端自动补前缀 |
| 明文 `ws://` 报 TLS 错误 `errno=118` | 组件 `transport` 默认 UNKNOWN 落入 SSL 分支 | 显式指定 `OVER_TCP` |
| 连接 29 秒后必断 | 缺心跳，服务端 30s 空闲超时 | 补 20 秒周期心跳 |
| 录音零上传 | `session_start` 未切 `ST_RECORDING`，循环条件立即为假 | 先置状态再建任务 |
| 板子音频下发瞬间黑屏重启 | 供电/热点稳定性（详见故障报告） | 排查中 |

完整记录见 [`docs/FAULT-blackout-and-playback.md`](zjy13/docs/FAULT-blackout-and-playback.md)

</details>

### yuanma —— 原厂固件源码逆向恢复

板子原本烧着别人编译的 `ai_pet_badge` 固件（Elysia 动画仪表盘），但**只有 16MB 二进制备份，没有源码**。

从二进制出发，最终完整复现了可编译的工程：

| 步骤 | 结果 |
|---|---|
| 动画素材恢复 | 从 3 个绿幕视频重新生成，**288 帧 / 6.9MB**，官方校验 PASS |
| 视频辨识 | 文件名是哈希，用逐帧人物高度/重心变化判定 walk/jump/sit |
| 编译 | 7944 KB，烧录地址 `0x20000` 与**原版分区表完全一致** |
| 实测启动 | `Assets v2 validated: 288 frames`，动画正常、触摸有反应 |
| 语音集成 | 新增 `voice_client.c`，复用 BSP 官方 codec 接口 |

---

## 🧩 其他项目速览

<details>
<summary><b>zjy9 —— 爱莉希雅 AI 聊天系统（功能最完整，973 行 README）</b></summary>

本仓库体量最大的项目，覆盖 PC 服务端 + 三个客户端：

- **服务端**：FastAPI，REST + WebSocket + 管理面板
- **模型来源**：本地 Ollama 优先，不可用自动切云端（DeepSeek / OpenAI / 通义 / 智谱 / Kimi）
- **记忆系统**：RAG 双后端（TF-IDF 轻量 / ChromaDB 精准）
  - 混合检索：TF-IDF 余弦 + BM25，用 RRF 融合 —— 纯向量对短查询会**一条都召回不了**
  - 分层记忆：短期窗口 / 中期摘要 / 长期事实
- **角色卡**：JSON 卡片，可切换多角色，**不用改代码**
- **用户学习**：知识图谱 + 画像演化，按设备隔离
- **情感引擎**：7 维情绪识别 + 趋势追踪
- **客户端**：Android（Kotlin + Compose）、树莓派 CLI、Web 管理面板

```bash
cd zjy9/server
pip install -r requirements.txt
python main.py          # 管理面板 http://localhost:8080/dashboard
```

</details>

<details>
<summary><b>zjy5 —— B站下载器 + 视频台词识别</b></summary>

一个目录里是**两件独立的事**：

**① `bilibili_downloader/`** —— 下载器，支持批量字幕收割（`--harvest`）
给一个 UP 空间 URL，自动枚举视频并探测字幕，命中的**直接抄文本**，不下载不 OCR。
带断点续跑（`harvest_state.json` 按 BV 号记状态）。

**② `chibtaici/`** —— 影片台词抽成纯文本 / SRT / jsonl
同时走**语音识别（whisper）**与 **OCR（PaddleOCR）** 两条路。
采集质量优化：`large-v3` + VAD + 专有名词提示词；OCR 跳帧（字幕静止不重跑，实测**省 88% 调用**）。

> ⚠️ 曾有 docstring 声称「支持说话人分离」，但**实际未实现**（参数是空壳）。已更正。
> `chibtaici/main.py` 里的 `import whisper` 是**有意的能力探测**，带 `# noqa: F401`，不要当成无用导入删掉。

</details>

<details>
<summary><b>zjy7 —— LoRA 微调流水线</b></summary>

```bash
cd zjy7
python prepare_data.py       # 生成训练数据
python train_lora.py         # LoRA 训练
python post_train.py --quant Q8_0   # 合并 → GGUF → 量化 → 测试
python evaluate.py           # 独立评估集评测
python deploy_ollama.py      # 部署到 Ollama
```

人设单一来源：`persona.py` 读 `zjy9/server/characters/elysia.json`，训练与部署共用。

</details>

<details>
<summary><b>zjy10 —— Anime Voice Lab（角色声线复刻，仅规划文档）</summary>

Windows + RTX 5070 + GPT-SoVITS 的角色声线复刻与可控语音生成。

| 文档 | 内容 |
|---|---|
| [`声音生成.md`](zjy10/声音生成.md) | 47 节：环境 → Zero-shot → 数据集 → 微调 → 情绪控制 → 评价 → GUI |
| [`技术选型与资源.md`](zjy10/技术选型与资源.md) | 42 节：7 个模型候选池、Adapter 架构、统一测试集 |

> ⚠️ 参考音频来自动漫角色时，实际是**配音演员的声音**。克隆产物不要入库。
> 该目录 `.gitignore` 已排除 `models/`、`dataset/`、`outputs/`、`characters/`、`configs/`。

</details>

<details>
<summary><b>zjy8 —— PC 推理 + 手机远程调用</summary>

让 PC 跑推理，手机当瘦客户端。两种方式：

- **`llama-server`**：`start_server.py`，默认端口 8080，**只监听 127.0.0.1**
- **Ollama**：端口 11434，用 `Modelfile` 导入 GGUF

> 如需暴露到局域网，**必须同时提供 `--api-key`**。

</details>

<details>
<summary><b>zjy6 / zjy11 / zjy2 / zjy12</summary>

- **zjy6**：Qwen 多端部署。Android 端（Kotlin+JNI 调 llama.cpp）需自行构建；**PC 端只有文档，`pc_server/` 是空目录**
- **zjy11**：ESP32-S3 云端角色对话**文字版**原型。网关可运行；固件源码骨架已实现但未烧录
- **zjy2**：次元萌盒产品方案。**没有可运行系统**，仅文档 + 计算器示例
- **zjy12**：设计决策台账（决策记录、评审、问题清单）+ `bench_llm.py` 本地 LLM 基准

</details>

---

## 📚 文档索引

### 跨项目分析（根目录 [`docs/`](docs/)）

> 该目录的说明与「审查范围已过时」提示见 [`docs/README.md`](docs/README.md)。

| 文档 | 内容 |
|---|---|
| [`代码审查报告.md`](docs/代码审查报告.md) | 静态审查 zjy2/5/6/7/8/9，**70 项问题**（高危 18 / 中危 30 / 低危 22）。⚠️ 写于 2026-09-21，无修复状态跟踪 |
| [`代码审查报告-zjy13-yuanma.md`](docs/代码审查报告-zjy13-yuanma.md) | **2026-11-05 补充**：zjy13 与 yuanma 的审查，**63 项问题**（高危 10 / 中危 27 / 低危 26） |
| [`功能完善与升级分析.md`](docs/功能完善与升级分析.md) | zjy5~zjy9 功能升级分析与实施记录 |
| [`改进与功能规划建议.md`](docs/改进与功能规划建议.md) | 全工作区改进建议，每条标注证据位置 |

### 其他目录入口

| 目录 | 说明 |
|---|---|
| [`yuanma/README.md`](yuanma/README.md) | 原厂固件源码说明（板子上实际运行的 `ai_pet_badge` 固件来源） |
| [`zjy12/README.md`](zjy12/README.md) | 设计决策与问题台账索引 |
| [`zjy13/docs/README.md`](zjy13/docs/README.md) | zjy13 技术文档本地索引（23 篇） |

### zjy13 技术文档（[`zjy13/docs/`](zjy13/docs/)，共 23 篇）

**🗓 工作记录**

| 文档 | 内容 |
|---|---|
| [`WORKLOG-2026-11-04.md`](zjy13/docs/WORKLOG-2026-11-04.md) | **最新**：无线直连改造、语音链路稳定性、24kHz 音质修复；含 7 个 Bug 与 7 条弯路 |
| [`WORKLOG-2026-11-02.md`](zjy13/docs/WORKLOG-2026-11-02.md) | 仓库同步、固件崩溃与分片缺陷修复、字库缺字、音量与长按交互 |
| [`development-log.md`](zjy13/docs/development-log.md) | 开发全记录（模块清单、关键决策、已知问题） |
| [`PROJECT-SUMMARY.md`](zjy13/docs/PROJECT-SUMMARY.md) | 完成情况总览、踩坑与修复、当前阻塞 |

**🐞 故障与审查**

| 文档 | 内容 |
|---|---|
| [`FAULT-blackout-and-playback.md`](zjy13/docs/FAULT-blackout-and-playback.md) | 故障报告：黑屏与播放中断；**5.0 节记录已证实的三个真实根因** |
| [`character-review.md`](zjy13/docs/character-review.md) | 角色卡审查：已修复 3 个缺陷 + 6 个待决策设计缺陷 |
| [`board-firmware-audit.md`](zjy13/docs/board-firmware-audit.md) | 板子与现有固件的不足审查 |
| [`firmware-flash-audit-plan.md`](zjy13/docs/firmware-flash-audit-plan.md) | 烧录前检查方案与问题清单 |

**🔌 协议与固件**

| 文档 | 内容 |
|---|---|
| [`protocol.md`](zjy13/docs/protocol.md) | WebSocket 通信协议（消息类型、时序、状态机、错误码） |
| [`firmware.md`](zjy13/docs/firmware.md) | ESP32-S3 固件说明（职责边界、数据流、编译烧录） |
| [`hardware-verification.md`](zjy13/docs/hardware-verification.md) | 1.85B 硬件资料核实（音频引脚、I2S 格式、4 声道采集） |
| [`esp32-board-report.md`](zjy13/docs/esp32-board-report.md) | 板子状况报告与固件备份 |

**✅ 分阶段验收**

| 文档 | 内容 |
|---|---|
| [`asr-plan.md`](zjy13/docs/asr-plan.md) / [`asr-report.md`](zjy13/docs/asr-report.md) | 阶段一：本地 ASR 方案与验收（RTF 0.009） |
| [`stage3-dialogue-loop.md`](zjy13/docs/stage3-dialogue-loop.md) | 阶段三：终端语音对话闭环 |
| [`tts-report.md`](zjy13/docs/tts-report.md) | 本地 TTS 验收报告（CosyVoice 3.0） |
| [`tts-performance.md`](zjy13/docs/tts-performance.md) | CosyVoice3 性能优化实测（含 `token_hop_len` 污染 Bug） |
| [`model-selection.md`](zjy13/docs/model-selection.md) | DeepSeek 模型选型报告 |

**🎨 音色与环境**

| 文档 | 内容 |
|---|---|
| [`voice-elysia.md`](zjy13/docs/voice-elysia.md) | 角色音色克隆：素材筛选、`<|endofprompt|>` 前缀坑 |
| [`v2-direction.md`](zjy13/docs/v2-direction.md) | V2 方案转向分析（本地 TTS 版） |
| [`wsl2-cosyvoice-guide.md`](zjy13/docs/wsl2-cosyvoice-guide.md) | WSL2 验证与 CosyVoice 部署指南 |
| [`wsl2-gpu-verified.md`](zjy13/docs/wsl2-gpu-verified.md) | WSL2 GPU 验证结果与网络配置 |

**📖 操作指南**

| 文档 | 内容 |
|---|---|
| [`demo-guide.md`](zjy13/docs/demo-guide.md) | 演示操作指南（网页 / 终端 / 报告三种方式 + 故障排查） |

---

## ⚠️ 本仓库不包含的内容

为控制体积，以下内容**没有入库**，需自行准备：

| 类别 | 说明 |
|---|---|
| 模型权重 | `models/`、`*.gguf`、`*.safetensors`、`*.bin` |
| 虚拟环境 | `venv/`、`.venv/`、`lora_env/` |
| 下载缓存 | `downloads/`、`ollama-models/` |
| 运行时数据 | 数据库、日志、加密密钥、`runtime_config.json` |
| 构建产物 | `*.jar`、`*.apk`、`__pycache__/`、`.gradle/`、固件 `build/` |
| **登录凭据** | `bilibili_cookies.txt`（含真实 SESSDATA，**永远不要提交**） |
| **固件备份** | `zjy13/backup/esp32/*.bin`（16MB×3，且**可能含原设备 Wi-Fi 凭据**） |
| **声音克隆数据** | `zjy10/` 下 `dataset/`、`characters/`、`outputs/`、`history/`、`configs/` |
| **他人仓库克隆** | `zjy6/android_app/skill-festival-submissions/`（有独立远程） |

> 各子目录下都有自己的 `.gitignore`，以那一份为准。

---

## 🔐 安全提示

- **`zjy5/bilibili_downloader/bilibili_cookies.txt`** 是真实 B 站登录凭据。
  如果曾提交过，请到 B 站「账号安全」注销登录 / 改密码，并清理 git 历史（`git filter-repo`）
- **`.env` / `.env.wifi` / `wifi_config.h`** 含 API 密钥与 Wi-Fi 密码，已被各目录 `.gitignore` 排除
- 后端 `api_token` 请通过 `PUT /api/config` 或 `config.py` 设置；设为非空后管理类接口都会要求认证
- `zjy8` 推理服务默认只监听 `127.0.0.1`；需暴露到局域网时必须提供 `--api-key`

---

## 🗓 最近更新

### 2026-11-04 — 无线直连 + 稳定性与音质修复

- **摆脱手机热点**：改用主机自带无线网卡开 Windows 移动热点（`Isla2Badge`，强制 2.4GHz），
  板子直连主机，网关固定 `192.168.137.1`，IP 不再漂移
- **开机自动连接**：WiFi 拿到 IP 后 **18ms** 自动连上服务端，板子插电即用，无需串口命令
- **修复上行卡死**：ESP32 默认 WiFi 省电在 WiFi Direct 热点下导致发送挂起，
  录音只上传 3 块即断 → 关闭省电后恢复 **68~72 块**
- **修复长音频断连**：服务端音频下发改为按播放进度节流（领先 2 秒），
  播放缓冲 20s→8s，16s+ 长音频 5 轮 0 失败
- **音质修复**：播放采样率 16kHz → **24kHz 直通**，消除降采样导致的
  8~12kHz 频段丢失、无抗混叠滤波的折返失真、重采样相位重置毛刺
- **内存优化**：回收三个超配静态缓冲，内部可用内存 **50.4KB → 79.2KB**，
  LCD 的 `ESP_ERR_NO_MEM` 报错从每轮 21~42 次降为 **0**
- **交互**：音量上屏（「语音待命 60」）、`VOL` 命令、长按阈值 400ms→300ms

> 详见 [`WORKLOG-2026-11-04.md`](zjy13/docs/WORKLOG-2026-11-04.md)

### 2026-10-30 — zjy13 语音链路打通 + 原厂固件复现

- **语音链路端到端首次跑通**：板子录音 → Wi-Fi 上传 → FunASR → DeepSeek → CosyVoice3 → 音频回传
- **修复 7 个固件缺陷**：状态覆盖、录音误初始化扬声器、采样率不一致、`transport` 未指定、
  缺心跳、播放阻塞网络回调、JSON 缓冲过小
- **原厂固件源码复现**：从 16MB 二进制恢复出可编译工程，动画素材重新生成并通过官方校验
- **音色精简**：删除内置 `xiaoman`，只保留 `elysia` 克隆音色
- 新增故障报告 [`FAULT-blackout-and-playback.md`](zjy13/docs/FAULT-blackout-and-playback.md)

### 2026-09-28 — zjy5 采集链路大改

- 新增 `--harvest` 批量字幕收割：UP 空间 → 自动枚举 + 探测字幕，命中直接抄文本
- 新增 `--format jsonl`：句级溯源（BV 号 + 时间点 + 通道 + 置信度）
- chibtaici：默认模型 `base` → `large-v3`，VAD 切句 + 专有名词提示词，OCR 跳帧省 88% 调用
- zjy7：人设改为单一来源，新增 `check_persona.py` 一致性自检

### 2026-09-21 — 全工作区代码审查

- 修复 70 项问题，详见 [`docs/代码审查报告.md`](docs/代码审查报告.md)
- zjy9 修复 39 项（认证绕过、历史取反、路由遮蔽、记忆清理失效、上下文溢出等）
- zjy8 厘清三个矛盾的 GGUF 补丁脚本（原 `fix_gguf.py` **会写坏模型文件**）

---

<div align="center">

**如果这个项目对你有启发，欢迎点个 ⭐ Star**

<sub>本项目仅供学习交流，涉及的游戏角色版权归原权利方所有</sub>

</div>
