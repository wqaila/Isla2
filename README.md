<div align="center">

# 赛博玩具 AI · 次元萌盒

**一个围绕「二次元 AI 陪伴」展开的个人项目集合**

从产品方案一路做到可运行的本地 LLM 聊天系统、角色语音克隆，再到 ESP32 硬件端语音交互

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![ESP-IDF](https://img.shields.io/badge/ESP--IDF-5.5-E7352C?logo=espressif&logoColor=white)
![Android](https://img.shields.io/badge/Android-Kotlin-3DDC84?logo=kotlin&logoColor=white)
![CUDA](https://img.shields.io/badge/CUDA-12.8-76B900?logo=nvidia&logoColor=white)
![Projects](https://img.shields.io/badge/子项目-12_个-blue)
![Tests](https://img.shields.io/badge/单元测试-538_+_479-brightgreen)
![Version](https://img.shields.io/badge/版本-4.12.0-e88bb5)

**当前里程碑**：[`v4.12.0`](../../releases) —— 真机联调闭环（2026-11-19）

</div>

---

## 📖 这是什么

这是一份持续了约四个半月的个人开发记录。目标很具体：**让一个动漫角色活在一块小屏幕上** —— 能听、能说、有声音、有性格。

围绕着这个目标拆出了 12 个子项目，各自解决一个环节：

```
想法/方案  →  本地大模型  →  角色人设  →  声音克隆  →  硬件端交互
 zjy2/zjy12    zjy6/zjy7/zjy8    zjy9      zjy10/zjy13      zjy11/zjy13
                                              └→ zjy14（引擎升级）
```

> **已完成**：板子插电即连、长按说话、爱莉希雅音色回放，整轮对话 7~12 秒。
> **2026-11-19**：真机联调闭环 —— 板子 → zjy13 → zjy14（CosyVoice3）整条链路
> 首次在真实硬件上跑通，按按钮出声已人工确认。

---

## 🚦 一句话看懂现状

| 问题 | 答案 |
|---|---|
| 现在能玩吗？ | 能。板子烧录 `yuanma` 固件，电脑跑 zjy13 + zjy14，长按说话即可 |
| 声音是谁的？ | 爱莉希雅（CosyVoice3 克隆，参考音频 9.70s / SNR 60.8dB） |
| 一轮对话多久？ | 7~12 秒（板子真实链路，含上传/合成/下发/播放） |
| 情绪控制能用吗？ | **不能**。实测 CosyVoice3-0.5B 的情绪指令无效，改走语速/音量/停顿 |
| 需要什么硬件？ | ESP32-S3-Touch-LCD-1.85B + 一台有 NVIDIA 显卡的电脑（实测 RTX 5070） |
| 最少要多少显存？ | ASR+TTS 同时驻留约 7.3GB / 12GB |

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
| 10 | [`zjy13`](zjy13/) | **ESP32-S3 语音交互系统**（板子已烧录联调通过） | FunASR + DeepSeek + CosyVoice3 + ESP-IDF | ✅ 可用 |
| 11 | [`zjy14`](zjy14/) | **Custom Voice Engine** · zjy13 的引擎升级 | CosyVoice3 + FastAPI（WSL2 / RTX 5070） | ✅ **真机在环** |
| 12 | [`yuanma`](yuanma/) | **原厂固件源码**（板子上实际运行的 `ai_pet_badge`） | ESP-IDF 5.5.5 / LVGL / ES7210+ES8311 | ✅ 可编译 |

状态图例：✅ 可运行 · ⚠️ 部分可用 · 📝 仅文档 · 🚧 开发中

> **关于 zjy13 与 zjy14 的关系**：zjy13 是完整产品（设备接入、ASR、LLM、对话编排），
> zjy14 只负责「文字转声音」。两者通过 HTTP 解耦，切换只需在 `zjy13/.env` 里改一行
> `TTS_BASE_URL`。详见 [§ zjy13 与 zjy14 的分工](#-zjy13-与-zjy14-的分工)。

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
| 端到端（终端链路） | 约 **4~5 秒**（电脑上跑 ASR→LLM→TTS） |
| 端到端（**板子真实链路**） | 约 **7~12 秒**（含录音上传、音频下发与播放） |
| 显存并发 | ASR+TTS 同时驻留 **7286 / 12227 MiB**，5 轮无泄漏 |
| 单元测试 | `.venv` **538 passed** / `.venv-asr` **538 passed** |
| WebSocket | 录音上传 170 块 / 599760 字节，**乱序 0 重复 0** |
| 下行音频 | 修复分片后 **134/134 帧、547200 字节**与服务端完全一致 |
| 长音频稳定性 | 16s+ 音频 **5 轮 0 失败**（下发按播放进度节流，领先 2 秒） |
| 上行稳定性 | 关闭 WiFi 省电后 **68~72 块 / transport 错误 0 次** |
| 内存 | 内部可用内存 **50.4KB → 79.2KB**，LCD 的 `ERR_NO_MEM` 每轮 21~42 次 → **0** |

> ⚠️ 两个「端到端」不是一回事：4~5 秒是**电脑终端闭环**，
> 板子链路多了上传、下发节流与播放，实测 **7~12 秒**。
> 引用时请带上限定词，别只写「端到端 4 秒」。

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
| 下行音频「只播约 1 秒就停」 | 434 字节帧被拆成多个 `DATA` 事件，未做分片重组 | 按 `payload_offset` 累积重组 |
| `IllegalInstruction` 崩溃 | `record_loop` 返回路径 `retw.n` 时 `A0=0` | 显式 `vTaskDelete(NULL)` + 清理悬空句柄 |
| 录音只上传 3 块即断 | ESP32 默认 WiFi 省电在 Windows 热点下挂起上行 | `esp_wifi_set_ps(WIFI_PS_NONE)` |
| 音质发闷、高频丢失 | 24k→16k 降采样无抗混叠滤波、相位每块重置 | 改为 **24kHz 直通**播放 |

> 💡 关于「黑屏」，最重要的结论是：**它一度被误判为供电不足**，
> 后来用 `addr2line` 反查崩溃地址 + 补分片诊断日志，才确认是**上面三个代码/配置缺陷**
> （崩溃返回路径、分片未重组、WiFi 省电），与供电裕量无关。
> 这段误判与纠正的完整过程保留在故障报告里。

完整记录见 [`zjy13/docs/FAULT-blackout-and-playback.md`](zjy13/docs/FAULT-blackout-and-playback.md)

</details>

<details>
<summary><b>🆕 2026-11-19 新增：填充音连发修复与 TTS 客户端加固（点击展开）</b></summary>

**填充音连响两三声**（用户反馈「板子会发出两声提示词，听起来不自然」）：

`_send_fillers` 的注释写着「边播边补」，但实现上**从未跟随播放进度** ——
它其实是一口气把最多 3 秒填充音全塞给板子。两个事实叠加：

| 事实 | 后果 |
|---|---|
| `should_continue()` 只看累计时长，不看是否已播完 | 发完第 1 声（0.70s）后条件仍成立，立刻发第 2 声 |
| `send_audio` 对填充音**没有节流** | 一个 0.7s clip = 9 帧，几毫秒内全写进 socket |

对比正文那边有 `lead_sec` 按播放进度节流，**填充音路径漏掉了这段**。
日志实测每轮发 4~5 声。

修复：发完一个就等它播完（用 `asyncio.wait` 而非 `sleep`，保证正文到达能立刻打断），
再加声数上限 1 声兜底。**实测 4~5 声 → 1 声**。

同时加固 TTS 客户端四项：

| 项 | 问题 | 修法 |
|---|---|---|
| `turn_id` 每 100 秒重复 | 毫秒对 100000 取模；碰撞后固件会**丢弃新帧**（表现为"偶尔没声音"） | 改用会话内递增序号 |
| 去重集合无界 | `_completed_turns` 只增不减 | 改成有界（保留最近 64 条） |
| 422 未映射 | zjy14 用 pydantic，校验失败是 422 而非 400；不可重试的错误被当可重试 | `(400, 422)` 都映射为参数错误 |
| 分块长度无上限 | 损坏长度会让缓冲区无界增长直到 120s 超时 | 加 96KB 上限，超限立刻报错 |

详见 [`zjy13/docs/FILLER_DOUBLE_SOUND_FIX.md`](zjy13/docs/FILLER_DOUBLE_SOUND_FIX.md)

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

## 🔗 zjy13 与 zjy14 的分工

这两个项目最容易混淆，因为都能「把文字变成声音」。实际上职责完全不同：

```
┌─────────────┐  Wi-Fi/WebSocket   ┌──────────────────────────────────┐
│  ESP32-S3   │◄─────────────────►│  zjy13（完整产品）                 │
│  录音/显示/播放 │   JSON + PCM      │  设备接入 · 协议 · ASR · LLM      │
└─────────────┘                    │  角色卡 · 对话编排 · 音频下发       │
                                   └───────────┬──────────────────────┘
                                               │ HTTP（只传文本，收 PCM）
                                               ▼
                                   ┌──────────────────────────────────┐
                                   │  zjy14（只做语音合成）             │
                                   │  CosyVoice3 · 音色 · 表达控制      │
                                   └──────────────────────────────────┘
```

| | zjy13 | zjy14 |
|---|---|---|
| **定位** | 端到端产品 | 语音引擎服务 |
| **职责** | 设备接入、协议、ASR、LLM、对话编排、音频收发 | 只负责「文字 → 声音」 |
| **代码量** | 79 个 Python 文件 / 约 13,771 行 | 132 个文件 / 约 20,197 行 |
| **测试** | 538 项 | 479 项 |
| **运行位置** | Windows + WSL（ASR） | WSL（GPU 推理） |
| **端口** | 8765（设备接入） | 50001（HTTP） |

**切换引擎只需一行**（在 `zjy13/.env` 里）：

```bash
TTS_BASE_URL=http://127.0.0.1:50001   # zjy14；改回 :50000 则是 zjy13 旧引擎
```

> ⚠️ 改完 `.env` **必须重启 zjy13 服务** —— 环境变量是进程启动时读入的。

**为什么 zjy14 代码更多但职责更单一**：它多了 Web 管理界面（React + TS 9 页）、
SQLite 历史记录库、配置校验层这些「产品化」的东西。

---

## 🧩 其他项目速览

<details>
<summary><b>zjy14 —— Custom Voice Engine（zjy13 的引擎升级，✅ 真机在环）</b></summary>

CosyVoice3 角色音色与表达控制引擎，目标硬件 RTX 5070 12GB（WSL2）。
复用 zjy13 已验证的固件、WebSocket 协议与角色卡，**只替换语音合成引擎**。

**8 个 Phase 全部完成**（Phase 0 审计 → Phase 7 稳定发布 → Phase 8 流式 UI）。
2026-11-19 补完了最后一个 P0：**真机联调**。

要解决的三个问题与实测结论：

| 问题 | 结论 |
|---|---|
| **情绪表达** | ❌ **实测无效**。官方 `inference_instruct2` 在带缓存音色时静默丢弃指令，且音频拉长 3.5 倍。已改走「路线乙」：语速/音量/停顿 + LLM 侧用词表达情绪 |
| **音色一致性** | ✅ 客观相似度 **0.9208**（超官方基准 0.7559），盲听确认改善 |
| **12GB 显存优化** | ✅ RTF **0.63**（比实时快），峰值显存 3528MB |

实测取舍：`precision=fp32`（**fp16 更慢 7.1%、显存 +27%**）。

```bash
# 全部命令在 WSL 中执行
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/start.sh cosyvoice3   # 启动引擎
wsl -e bash /mnt/d/xiangmudaima/zjy14/probe/run_device_e2e.sh        # 真机联调验收
```

**479 个单元测试全绿**，8 个 Phase 的验收脚本全部通过。

**能力边界（诚实记录）**：

- **情绪指令无效** —— 不是工程问题，是 0.5B 模型的能力上限。详见 [`docs/13_D1_EMOTION_VERDICT.md`](zjy14/docs/13_D1_EMOTION_VERDICT.md)
- **仅 1 个角色素材**（elysia），无法验证「跨角色不串音色」
- **音色已改善但非完美** —— 剩余差距可能来自参考素材代表性

完整文档见 [`zjy14/docs/`](zjy14/docs/)（31 篇）。

</details>

<details>
<summary><b>zjy9 —— 爱莉希雅 AI 聊天系统（功能最完整，README 约 1250 行）</b></summary>

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
| [`版本迭代史.md`](docs/版本迭代史.md) | **项目历程与版本迭代史**：`1.0.0`（2026-07-01）→ `4.12.0`（2026-11-19），34 个版本，含版本号判定规则、全局总表、时间线与逐版本详情。**已补打 6 个里程碑 tag** |
| [`30_DAILY_SUMMARY_2026-11-19.md`](docs/30_DAILY_SUMMARY_2026-11-19.md) | **最新**：真机联调闭环 + 4 个 P0 级静默失效修复 + 界面主题化；含诚实记录的失败与被推翻结论 |
| [`代码审查报告.md`](docs/代码审查报告.md) | 静态审查 zjy2/5/6/7/8/9，正文 **106 项**（严重 2 / 高 32 / 中 52 / 低 20）。⚠️ 写于 2026-09-21，无修复状态跟踪 |
| [`代码审查报告-zjy13-yuanma.md`](docs/代码审查报告-zjy13-yuanma.md) | zjy13 与 yuanma，**高 15 / 中 41 / 低 15 条编号**（11-05 初版 + 11-09 补充审计） |
| [`代码审查报告-补充审计-2026-11-09.md`](docs/代码审查报告-补充审计-2026-11-09.md) | 补审过程记录：4 个单元产出、去重、编号映射、未采纳条目 |
| [`代码审查修复台账.md`](docs/代码审查修复台账.md) | **两份报告的修复状态**（旧报告已证实修复 7 项；zjy13/yuanma 高危 15 条已逐条核对：1 误报 / 1 已修 / 1 部分 / 12 仍存在） |
| [`功能完善与升级分析.md`](docs/功能完善与升级分析.md) | zjy5~zjy9 功能升级分析与实施记录（范围仅 5 个，不含 zjy13/yuanma） |
| [`改进与功能规划建议.md`](docs/改进与功能规划建议.md) | 工作区改进建议，每条标注证据位置。写于 2026-09-23，**不含 zjy11/zjy13/yuanma** |

### 其他目录入口

| 目录 | 说明 |
|---|---|
| [`yuanma/README.md`](yuanma/README.md) | 原厂固件源码说明（板子上实际运行的 `ai_pet_badge` 固件来源），含上游 8 篇文档索引 |
| [`zjy12/README.md`](zjy12/README.md) | 设计决策与问题台账索引 |
| [`zjy13/docs/README.md`](zjy13/docs/README.md) | zjy13 技术文档本地索引（30 篇 + [`asr/README.md`](zjy13/asr/README.md)） |
| [`zjy14/docs/`](zjy14/docs/) | zjy14 技术文档（31 篇）：音色诊断、情绪验证、Phase 报告、真机联调、集成审计 |
| [`zjy11/README.md`](zjy11/README.md) | zjy11 项目说明；第 0 节是该项目的文档索引（9 篇 docs + [`RUNNING.md`](zjy11/RUNNING.md) + [`firmware/README.md`](zjy11/firmware/README.md) |

### zjy13 技术文档（[`zjy13/docs/`](zjy13/docs/)，共 30 篇）

**🗓 工作记录**

| 文档 | 内容 |
|---|---|
| [`FILLER_DOUBLE_SOUND_FIX.md`](zjy13/docs/FILLER_DOUBLE_SOUND_FIX.md) | **最新**：填充音连发缺陷（板子连响两声语气词）根因与修复 |
| [`WORKLOG-2026-11-04.md`](zjy13/docs/WORKLOG-2026-11-04.md) | 无线直连改造、语音链路稳定性、24kHz 音质修复；含 7 个 Bug 与 7 条弯路 |
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

### 2026-11-19 — 真机联调闭环 + 4 个 P0 级静默失效修复 + 界面主题化（`v4.12.0`）

**补完了长期挂起的 P0：真机联调。** 此前日报里这条一直是「需要 ESP32 硬件」，
Phase 5 也被收敛成「桥接验证」（协议对齐 + FakeDevice 模拟，没接真板子）。
今天板子在线，整条链路首次在真实硬件上闭环，**按按钮出声已人工确认**。

**4 个 P0 级静默失效**（共同点：不抛异常、不打错误日志，只是结果不对）：

| 缺陷 | 表现 | 根因 |
|---|---|---|
| 天气不显示 | 屏幕 `--°`，被误判成"没连 WiFi" | Windows ICS DNS 代理回 RST，`getaddrinfo()` 返回 202 |
| 填充音连响 | 板子连着 2~3 声「嗯…」「这个嘛…」 | `_send_fillers` 声称"边播边补"，实际一口气塞满 3 秒 |
| 排队超时 | HTTP 200 + 空音频，日志无错 | 只发「长度 0」结束标记，客户端把它当正常完成 |
| stub 无音频 | 默认配置下流式永远无声 | `if chunk.is_final: break` 丢掉了唯一带音频的块 |

**7 项集成缺陷**：`turn_id` 每 100 秒重复（导致固件丢弃新帧）、去重集合无界、
超时预算与队列等待相等、422 未映射、流式无音色预校验、
`pause_profile` 默认值改变韵律、分块长度无上限。

**界面主题化**：依据爱莉希雅官方设定（粉色主调、粉白定调、紫色装饰、♪、花/蝴蝶/群星）。
但这是运维监控界面，保持暗色底保证数字可读。

**测试**：zjy13 **538 项**、zjy14 **479 项**，全部通过。

**同时诚实记录了两项被实测推翻的结论**（避免以后重复怀疑）：

1. 「固件没实现 0 长帧结束协议」—— 不成立。审查看的是 `zjy13/firmware/`，
   **不是真正烧录的那份**（`yuanma/AI-Pet-Badge-main`，device_id 也不同）
2. 「流式接口只返回 4 字节」—— 不成立。是 `Invoke-WebRequest` 处理二进制流的问题

详见 [`docs/30_DAILY_SUMMARY_2026-11-19.md`](docs/30_DAILY_SUMMARY_2026-11-19.md)。

### 2026-11-18 — 设备状态监控 Web 端 + 历史记录三轮补齐（`v4.9.0` ~ `v4.11.0`）

- **设备状态监控**：zjy13 新增只读 `/api/devices` 端点（复用 WS 端口的
  `process_request` 钩子），zjy14 新增实时监控页，每 1 秒刷新
- **合成记录与日志查询 API** + 前端两页
- **历史记录三轮补齐**：FTS5 全文搜索（中文需 trigram 分词器）、CSV/JSON 导出、
  归档、自动清理、相关度排序、日志审计

### 2026-11-16 — zjy14 引擎升级完成（`v4.6.0` ~ `v4.8.0`）

- **Phase 2b 表达控制**（路线乙）：语速 / 音量 / 停顿 + 指令解析器
- **D1 情绪验证结论**：**CosyVoice3-0.5B 的情绪指令无效**，改走路线乙
- **Phase 4 流式与并发**：长句首包 5.36s → 1.48s（+72.4%）
- **Phase 7 稳定发布**：30 分钟 596 轮零失败、显存零增长

### 2026-11-12 — 流式 TTS + 填充音（`v4.4.0` ~ `v4.5.0`）

- **流式 TTS**：首块 5106ms → 1685ms
- **填充音**：松手到有声音 2.3s → 3ms（本轮修复了它的连发缺陷）
- 音量 60 → 85

### 2026-11-09 — zjy13 修复盘查发现的 12 项问题 + 一键启动与 CI

- 按审查台账逐条修复 12 项，含固件崩溃与协议边界问题
- 新增**一键启动脚本**，降低复现门槛
- 新增 GitHub Actions CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)），自动跑单元测试

### 2026-11-09 — zjy14 当日完整总结

- 新增 [`zjy14/docs/`](zjy14/docs/) 当日工作记录（成果与不足都写了）
- 启用 **wetext 文本前端**（无需 modelscope token）
- 复测结论如实记录：**补全参考文本后语速修正，但相似度未提升**

### 2026-11-09 — 高危项逐条复核

- **15 条高危全部按文件行号读过代码**：1 条误报、1 条已修、1 条部分修复、12 条仍存在
- **H1 是误报**：报告称下行音频 `seq` 用 1 字节打包会回绕，但代码从 `ed08eaf` 起就是
  `struct.pack("<BBHII", ...)`——`seq` 一直是 2 字节，初版把第二个 `H` 看漏了。**不必修**
- **H7 已修**：播放任务改为自删（`vTaskDelete(NULL)`），不再被外部删除
- **H5 部分修复**：已加 `s_ws_lock` 互斥量，但 `destroy` 后仍不等待发送方退出
- **中危抽查 8 条（M2/M3/M4/M5/M6/M9/M20/M23）全部仍存在**
- 结论与证据全部写入 [`docs/代码审查修复台账.md`](docs/代码审查修复台账.md)

### 2026-11-09 — 补充审计：补齐缺失的 8 项中危，新增 36 条

- **重跑 4 个审计单元**（WebSocket/协议、LLM/TTS、对话+固件、yuanma 固件），
  逐行阅读约 1.35 万行自研代码，共产出 55 条，合并去重后 **34 条**
- 初版丢失的 `M20`–`M27` 已补齐，另新增 14 项中危、5 项高危、15 条编号化的低危
- 现状：**高危 15 / 中危 41 / 低危 15 条编号**，编号连续无缺口
- 新发现的高危含：固件按「声明长度」写音频导致读越界（H11）、心跳误判使设备永久离线（H12）、
  分片丢弃后帧错位（H13）、`voice_start` 失败致自动重连永久失效（H14）、
  `voice_stop` 与发送并发 use-after-free（H15）
- 过程记录见 [`docs/代码审查报告-补充审计-2026-11-09.md`](docs/代码审查报告-补充审计-2026-11-09.md)，
  含去重、编号映射与 **未采纳条目及理由**
- ⚠️ 补充条目为自动审计产出，抽样复核 5 条中 **4 条成立、1 条误报已剔除**；修复前请自行确认行号

### 2026-11-09 — 文档缺口清理

- **订正两份审查报告的统计**：旧报告头部写「70 项」但正文实际编号到 **106 项**
  （严重 2 / 高 32 / 中 52 / 低 20），已按正文重数并注明原因
- **给 63 项报告补上完整性说明**（当日仅标注缺口，内容于同日后续补审补齐，见上一条）：
  中危只收录 `M1`–`M19`（缺 `M20`–`M27` 共 8 项）、
  低危 26 项无编号只有类别概述、`yuanma` 审计单元输出被截断——均已在正文标注
- **新建** [`docs/代码审查修复台账.md`](docs/代码审查修复台账.md)：把「问题编号 ↔ 修复状态」
  单独成册。回填后确认 **7 项已修复**（原文档只承认 3 项）、2 项已复核仍存在
- **补索引入口**：zjy13 `asr/README.md`、development-log 文档清单（16 → 24 篇）、
  zjy6 两篇指南、zjy9 三个子目录 README 与 `docs/specs/`、zjy2 两篇方案、yuanma 上游 8 篇文档
- **订正过时记录**：`PROJECT-SUMMARY` 第六阶段「受网络阻塞」→ 已完成；
  「端到端 4~5 秒」补齐为**终端链路**，板子真实链路标为 7~12 秒；
  `改进与功能规划建议.md` 标注其范围止于 2026-09-23（不含 zjy11/13/yuanma）；
  `zjy12` 设计文档里指向不存在的 `voice_agent/README.md`、`esp32/README.md` 的说明

### 2026-11-05 — 补充 zjy13 与 yuanma 代码审查

- 新增 [`docs/代码审查报告-zjy13-yuanma.md`](docs/代码审查报告-zjy13-yuanma.md)：
  按模块拆 5 个审计单元并行静态阅读，记录 63 项（高危 10 / 中危 27 / 低危 26）
- 高危含：下行音频 seq 单字节打包回绕、LLM 读超时恒小于总超时（连带重复计费）、
  取消分支成死代码、播放任务临界区被删除（随机复位）、明文 `ws://` + 空 token
- ⚠️ 其中 8 项中危（`M20`–`M27`）与 26 项低危**未逐条落盘**——已于 2026-11-09 补充审计补齐

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
