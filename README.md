# 赛博玩具 AI · 次元萌盒 —— 项目工作区

一个围绕「二次元 AI 陪伴」展开的个人项目集合，从产品方案一路做到可运行的本地 LLM 聊天系统。

> 本仓库用分支管理不同阶段的工作：`master` 是当前的工作区快照（含全部 7 个子项目），
> 历史上还有 `tiqutaici`（台词提取）、`main` 等分支。

---

## 目录一览

| 目录 | 项目 | 技术栈 | 状态 |
|------|------|--------|------|
| [`zjy2/`](zjy2/) | **次元萌盒** —— 桌面级 AI 互动硬件原型 | 产品文档 + 示例代码 | 方案设计阶段，未实现 |
| [`zjy5/`](zjy5/) | **B站下载器 + 视频台词识别** | Python / yt-dlp / Playwright / Whisper / PaddleOCR | 可用 |
| [`zjy6/`](zjy6/) | **Qwen/Gemma 多端本地部署** | Android(Kotlin+JNI) / llama.cpp | Android 端需自行构建；PC 端仅有文档 |
| [`zjy7/`](zjy7/) | **爱莉希雅 LoRA 微调** | transformers / peft / Qwen2.5-7B | 流水线完整 |
| [`zjy8/`](zjy8/) | **PC 本地推理 + 手机远程调用** | Python / llama-server / Ollama | 可用 |
| [`zjy9/`](zjy9/) | **爱莉希雅 AI 聊天系统** | FastAPI + Android + 树莓派 + 网页 | 功能最完整 |
| [`zjy10/`](zjy10/) | **Anime Voice Lab** —— 动漫角色声线复刻 | 文档（规划中）/ GPT-SoVITS | 规划阶段，仅文档 |

另外根目录还有一份 [`代码审查报告.md`](代码审查报告.md)，
记录了对前 6 个项目做代码审查时发现的 70 项问题及修复建议。

---

## 各项目速览

### zjy9 —— 爱莉希雅 AI 聊天系统（主力项目）

基于本地大语言模型 + 云端 API 的角色扮演聊天系统：

- **PC 端服务**：FastAPI，提供 REST + WebSocket + 管理面板
- **模型来源**：本地 Ollama 优先，不可用时自动切到云端（DeepSeek / OpenAI / 通义 / 智谱 / Kimi）
- **记忆系统**：RAG 双后端（TF-IDF 轻量 / ChromaDB 精准）
  - **混合检索**：TF-IDF 余弦 + BM25，用 RRF 融合 —— 纯向量对短查询会**一条都召回不了**
  - **分层记忆**：短期窗口（最近 N 条）/ 中期摘要（会话变长后自动生成）/ 长期事实
- **角色卡**：人设是 JSON 卡片，可切换多角色，**不用改代码**
- **用户学习**：知识图谱 + 画像演化，按设备隔离
- **情感引擎**：7 维情绪识别 + 趋势追踪，动态调整回复语气
- **客户端**：Android（Kotlin + Compose）、树莓派 CLI、Web 管理面板
- **运维**：对话导出（JSON / Markdown）、数据库自动备份、WebSocket 鉴权、生成并发闸门

```bash
cd zjy9/server
pip install -r requirements.txt
python main.py          # 或 start.bat
# 管理面板 http://localhost:8080/dashboard
```

三个客户端分别在 `zjy9/android/`、`zjy9/rpi_client/`、`zjy9/server/static/`（面板）。
另外 **`zjy9/website/` 是对外的独立展示站**（项目介绍页 + 全景汇总），
后端**刻意不提供**它 —— 后端只挂载 `server/static/`。

### zjy7 —— LoRA 微调流水线

```bash
cd zjy7
pip install -r requirements.txt
python prepare_data.py       # 生成训练数据
python data_stats.py         # 数据统计（检查分布）
python train_lora.py         # LoRA 训练
python post_train.py --quant Q8_0   # 合并 → GGUF → 量化 → 测试
python evaluate.py           # 独立评估集评测
python deploy_ollama.py      # 部署到 Ollama（可选）
```

### zjy5 —— B站下载器 + 视频台词识别

一个目录里其实是**两件独立的事**。

**① `bilibili_downloader/` —— 下载器**

```bash
cd zjy5/bilibili_downloader
python -m bilibili_downloader --help
```

支持下载视频 / 音频、按频道或搜索结果批量下载、提取 AI 字幕（纯文本或 SRT）。
内置 WBI 签名，用 Playwright 处理登录。

**② `chibtaici/` —— 视频台词识别**

把视频里的台词抽成**纯文本**（无时间戳）：

```bash
cd zjy5/chibtaici
python main.py
```

能力比"OCR"广 —— 同时走 **语音识别（whisper / faster-whisper）** 与
**OCR（PaddleOCR）** 两条路，两者可以单独启用或同时用。

> ⚠️ 代码的模块 docstring 一度声称"支持说话人分离 / 多人声识别"，
> 但**实际并未实现**（`speaker_diarization` 参数只是个空壳，依赖里也没有
> 任何声纹/分离库）。该 docstring 已更正，别按那个说法规划用途。

> `chibtaici/main.py` 里的 `import whisper` 是**有意的能力探测**（缺失时降级到
> OCR 路径），带 `# noqa: F401`，**不要当成无用导入删掉**。

### zjy6 —— Qwen/Gemma 多端本地部署

两条路线：

- **方案一：Android 手机部署**（推荐）—— `android_app/`，Kotlin + JNI 调 llama.cpp
- **方案二：PC 端推理** —— **只有文档**（`deploy_android.md`、
  `merge_and_convert_model.md`），**`pc_server/` 是空目录，尚无实现**

辅助脚本：`download_qwen_model.py`（下模型）、`merge_lora_only.py`、
`convert_to_gguf.py`（合并 LoRA → GGUF）。

### zjy8 —— PC 本地推理 + 手机远程调用

zjy8 的定位是**部署方案**，不是补丁集 —— 让 PC 跑推理、手机当瘦客户端。
（"GGUF 修复"只是它历史上解决的问题，不是当前用途。）

- **方式 A：`llama-server`** —— `start_server.py`，默认端口 8080，默认只监听 `127.0.0.1`
- **方式 B：Ollama** —— 默认端口 11434，用 `Modelfile` 导入 GGUF

> 如需暴露到局域网，**必须同时提供 `--api-key`**。

### zjy10 —— Anime Voice Lab（动漫角色声线复刻）

Windows + RTX 5070 + GPT-SoVITS 的动漫角色声线复刻与可控语音生成系统。
**目前只有规划文档，尚无代码**：

| 文档 | 内容 |
|------|------|
| [`声音生成.md`](zjy10/声音生成.md) | 主体流程（47 节）：环境搭建 → Zero-shot → 数据集 → 微调 → 声线参数化 → 情绪控制 → 后处理 → 声音评价 → 自动调参 → GUI |
| [`技术选型与资源.md`](zjy10/技术选型与资源.md) | 独立补充（42 节）：开源生态选型、7 个模型候选池、Model Controller + Adapter 架构、统一测试集与「任务→模型适配关系」 |

> ⚠️ 参考音频如果来自动漫角色，实际是**配音演员的声音**。
> 克隆产物（参考音频 / 训练好的模型 / 生成结果）**不要入库** ——
> 该目录的 `.gitignore` 已排除 `models/`、`dataset/`、`outputs/`、
> `history/`、`characters/`、`configs/`。理由见文档第四十七节。

### zjy2 —— 次元萌盒

桌面级 AI 互动硬件的**产品方案**，属于方案设计阶段，**没有可运行的系统**：

| 文件 | 内容 |
|------|------|
| `1.md` | 项目方案（产品概述与核心功能规划） |
| `最小原型说明书.md` | 最小原型（MVP）制作与技术实现说明书 |
| `README.md` | 项目定位（面向二次元爱好者的桌面级 AI 互动玩具） |
| `Isla2/calculator.py` + `test_calculator.py` | 示例代码与单元测试，仅用于说明思路 |

> 目录下有一个**遗留的 `.git`**（工作树是子项目目录，与远端根布局不一致），
> 属于重构前的残留，**不要依赖它**。

---

## ⚠️ 本仓库不包含的内容

为了控制仓库体积，以下内容**没有入库**，需要自行准备：

| 类别 | 说明 |
|------|------|
| 模型权重 | `models/`、`*.gguf`、`*.safetensors`、`*.bin` |
| 虚拟环境 | `venv/`、`.venv/`、`lora_env/` |
| 下载缓存 | `downloads/`、`ollama-models/` |
| 运行时数据 | 数据库、日志、加密密钥、`runtime_config.json` |
| 构建产物 | `*.jar`、`*.apk`、`__pycache__/`、`.gradle/`、Gradle 发行包 |
| **登录凭据** | `bilibili_cookies.txt`（含真实 SESSDATA，**永远不要提交**） |
| **声音克隆数据** | `zjy10/` 下的 `dataset/`、`characters/`、`outputs/`、`history/`、`configs/` —— 参考音频来自动漫角色时属于配音演员的声音，不适合公开分发 |

> 各子目录下都有自己的 `.gitignore`，以那一份为准。

---

## 安全提示

- **`zjy5/bilibili_downloader/bilibili_cookies.txt`** 里是真实的 B 站登录凭据。
  如果你曾经把它提交过，请到 B 站「账号安全」里注销登录 / 改密码，
  并清理 git 历史（`git filter-repo`）。
- 后端服务的 `api_token` 请通过 `PUT /api/config` 或 `config.py` 设置；
  设置为非空后，管理类接口（`/api/config`、`/api/memory/*`、`/api/learning/*` 等）
  都会要求认证。
- `zjy8` 的推理服务默认只监听 `127.0.0.1`；如需暴露到局域网，必须同时提供 `--api-key`。

---

## 最近更新

**2026-09-24** —— 新增 `zjy10`（Anime Voice Lab）；zjy9 完成多轮功能补强：

- 新增 `zjy10/`：声音克隆项目的两份规划文档 + `.gitignore`
  （数据目录全部排除，见上方"本仓库不包含的内容"）
- zjy9：混合检索（BM25 + 向量，修复短查询召回为空的 bug）、分层记忆中期摘要、
  角色卡（多角色切换）、对话导出 + 数据库备份、记忆单条删除 + 管理页、
  数据库迁移机制、WebSocket 鉴权、生成并发闸门、`/ready` 探针
- zjy9：修掉一个影响面很大的 bug —— **`ollama_client` 没关 `trust_env`**，
  导致只要系统配了代理，本机 Ollama 全部请求返回 502
- zjy9：Android 端构建跑通（需 JDK 17 + 全局代理，`dl.google.com` 在分流模式下会被拦）

**2026-09-21** —— 完成全工作区代码审查与集中修复：

- 修复 70 项问题（高危 18 / 中危 30 / 低危 22），详见 [`代码审查报告.md`](代码审查报告.md)
- zjy9：修复认证绕过、对话历史取反、路由遮蔽、记忆清理失效、上下文溢出、流式重复输出、多用户画像串号等 39 项
- zjy5：打通 CLI 入口、实现 WBI 签名、修复画质参数与字幕 cid、恢复 TLS 校验
- zjy6：修复 Android 权限申请、prompt 重复、流式不显示、token 缓冲写死等问题
- zjy7：重建独立评估集、修复路径硬编码、补随机种子、修复 Modelfile 缺 TEMPLATE
- zjy8：厘清三个互相矛盾的 GGUF 补丁脚本（**原 `fix_gguf.py` 会写坏模型文件**）、补 requirements、默认监听回环地址
- zjy2：删除空文件、修复跑不起来的单元测试
