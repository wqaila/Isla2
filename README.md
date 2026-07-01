# 爱莉希雅 AI - 本地 LLM 聊天系统

基于本地大语言模型 + 云端 API 的角色扮演 AI 聊天系统，支持 Android 手机、树莓派客户端远程调用电脑进行推理。AI 回复自动以爱莉希雅（崩坏3）的角色风格输出，内置 RAG 记忆系统让 AI 能记住过去的对话，情感引擎让回复更具同理心。**智能体通过持续对话逐步学习用户习惯、构建记忆神经网络，实现越来越个性化的回复。**

## 项目结构

```
d:\zjy9\
├── server/                         # PC 端后端服务
│   ├── main.py                     # FastAPI 主入口（REST API + WebSocket + 管理面板）
│   ├── config.py                   # 服务配置（端口、Ollama、云端 API、记忆后端等）
│   ├── database.py                 # SQLite 数据库管理（4 张表）
│   ├── ollama_client.py            # Ollama 本地模型客户端
│   ├── cloud_client.py             # 云端 API 客户端（支持 6 个提供商）
│   ├── elysia_prompt.py            # 爱莉希雅人设 System Prompt + Few-Shot 示例
│   ├── memory.py                   # RAG 记忆系统（双后端：TF-IDF / ChromaDB）
│   ├── user_learning.py            # 用户学习引擎（记忆神经网络 + LLM 辅助分析）
│   ├── emotion_engine.py           # 情感识别引擎（多维度情感分析 + 趋势追踪）
│   ├── config_persistence.py       # 运行时配置持久化（JSON 文件）
│   ├── connection_manager.py       # WebSocket 连接管理器
│   ├── logger_service.py           # 统一日志服务（控制台 + 数据库 + WebSocket）
│   ├── requirements.txt            # Python 依赖
│   ├── start.bat                   # Windows 一键启动脚本
│   ├── start_tunnel.bat            # Cloudflare Tunnel 公网隧道启动脚本
│   ├── cloudflared-windows-amd64.exe  # Cloudflare Tunnel 客户端工具
│   ├── venv/                       # Python 虚拟环境（已配置）
│   ├── static/
│   │   └── index.html              # Web 管理面板（单页应用）
│   └── data/                       # 运行时数据
│       ├── elysia_server.db        # SQLite 数据库
│       ├── memory_tfidf/           # TF-IDF 记忆存储
│       ├── memory_db/              # ChromaDB 记忆存储
│       └── logs/                   # 日志文件
│
├── android/                        # Android 客户端
│   ├── app/
│   │   ├── src/main/
│   │   │   ├── AndroidManifest.xml
│   │   │   ├── java/com/elysia/ai/
│   │   │   │   ├── MainActivity.kt
│   │   │   │   ├── data/Models.kt
│   │   │   │   ├── data/PreferencesManager.kt
│   │   │   │   ├── network/WebSocketClient.kt
│   │   │   │   ├── viewmodel/ChatViewModel.kt
│   │   │   │   └── ui/
│   │   │   │       ├── theme/ (Color/Theme/Type)
│   │   │   │       └── screens/ (ChatScreen/SettingsScreen)
│   │   │   └── res/
│   │   ├── build.gradle.kts
│   │   └── proguard-rules.pro
│   ├── build.gradle.kts
│   ├── settings.gradle.kts
│   ├── gradle.properties
│   └── gradle/wrapper/gradle-wrapper.properties
│
├── rpi_client/                     # 树莓派客户端
│   ├── elysia_client.py            # 树莓派主客户端
│   ├── elysia_cli.py               # 命令行交互工具
│   ├── setup.py                    # 安装脚本
│   ├── install.sh                  # 一键安装
│   ├── requirements.txt            # Python 依赖
│   └── README.md                   # 说明文档
│
├── model-Q8_0.gguf                 # GGUF 量化模型文件
├── Modelfile                       # Ollama 模型定义文件
├── .gitignore                      # Git 忽略规则
└── README.md                       # 本文档
```

## 系统架构

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           系统整体架构                                    │
│                                                                          │
│  ┌─────────────┐  WiFi/USB/公网   ┌──────────────────────────────┐      │
│  │  Android App │ ◄══════════════► │     PC 服务端 (FastAPI)       │      │
│  │  (Kotlin)    │  WebSocket       │                              │      │
│  │             │  (WSS/WS)        │  ┌─ Ollama (本地GPU)          │      │
│  │  • 聊天界面  │                  │  ├─ 云端 API (6家)           │      │
│  │  • 设置界面  │                  │  └─ 统一路由 auto            │      │
│  │  • 流式输出  │                  │                              │      │
│  │  • 公网连接  │                  │  • 人设 Prompt 注入           │      │
│  └─────────────┘                  │  • RAG 记忆系统               │      │
│                                   │  • 情感引擎                    │      │
│  ┌─────────────┐                  │  • 日志 & 监控面板            │      │
│  │  浏览器/PC   │ ◄══════════════► │  • SQLite 持久化              │      │
│  │  Web 管理面板│  HTTP/HTTPS      └──────────────────────────────┘      │
│  └─────────────┘                          │                              │
│                                            │ Cloudflare Tunnel           │
│                                            ▼                              │
│                                   ┌──────────────────────┐               │
│                                   │  Cloudflare 网络      │               │
│                                   │  (免费公网 HTTPS)     │               │
│                                   │  自动分配域名         │               │
│                                   └──────────────────────┘               │
│                                            │                              │
│                            ┌───────────────┼───────────────┐             │
│                            ▼               ▼               ▼             │
│                     ┌──────────┐    ┌──────────┐    ┌──────────┐         │
│                     │ 公网手机  │    │ 公网手机  │    │ 公网浏览器│         │
│                     │  App     │    │  App     │    │          │         │
│                     └──────────┘    └──────────┘    └──────────┘         │
└──────────────────────────────────────────────────────────────────────────┘
```

## 快速开始

### 1. 启动 PC 端服务

```bash
# 方式一：双击 server/start.bat（自动创建虚拟环境）
# 方式二：命令行
cd server
venv\Scripts\pip.exe install -r requirements.txt
venv\Scripts\python.exe main.py
```

启动后：
- 管理面板：http://localhost:8080/dashboard
- API 文档：http://localhost:8080/docs

### 2. 确保 Ollama 已运行（本地模型）

```bash
ollama serve          # 如果未运行
ollama list           # 查看已安装模型
```

### 3. 配置云端 API（可选）

在管理面板的「云端 API」页面配置，或通过 API：

```bash
# 通过 API 设置（推荐 DeepSeek，性价比最高）
curl -X POST http://localhost:8080/api/cloud/config \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "deepseek",
    "api_key": "sk-your-api-key-here"
  }'

# 测试连接
curl -X POST http://localhost:8080/api/cloud/test
```

支持的云端提供商：

| 提供商 | 说明 | 默认模型 |
|--------|------|----------|
| DeepSeek | 性价比极高，国内访问快 | deepseek-chat |
| OpenAI | 效果最好 | gpt-4o |
| 通义千问 | 阿里云，国内访问快 | qwen-plus |
| 智谱 GLM | 免费额度大 | glm-4-flash |
| 月之暗面 Kimi | 长文本能力强 | moonshot-v1-8k |
| 自定义 | 任意 OpenAI 兼容 API | - |

### 4. 打开 Android App

1. 用 Android Studio 打开 `android/` 目录
2. 构建并安装到手机
3. 在设置中输入电脑 IP 地址（如 `192.168.1.50`）和端口 `8080`
4. 点击「连接」开始聊天

## 公网访问（Cloudflare Tunnel）

支持通过 Cloudflare Tunnel 将服务暴露到公网，让**不在同一局域网的用户**也能访问。

### 原理

```
PC (localhost:8080) → cloudflared 隧道 → Cloudflare 网络 → 公网 HTTPS 地址
```

- **免费**，无需购买域名或服务器
- **自动 HTTPS**，WebSocket 通过 WSS 协议安全传输
- **无需开端口**，不需要配置路由器或防火墙

### 使用步骤

#### 1. 启动 PC 端服务

```bash
cd server
start.bat
```

#### 2. 启动 Cloudflare Tunnel（新开一个终端）

```bash
# 方式一：双击 server/start_tunnel.bat
# 方式二：命令行
cd server
cloudflared-windows-amd64.exe tunnel --url http://localhost:8080
```

终端会输出类似：
```
Your quick Tunnel has been created! Visit it at:
https://abc-def-123.trycloudflare.com
```

#### 3. 公网用户访问

| 访问方式 | 操作 |
|----------|------|
| **浏览器** | 直接打开 `https://abc-def-123.trycloudflare.com/dashboard` |
| **Android App** | 设置 → 开启「公网地址」→ 粘贴 `https://abc-def-123.trycloudflare.com` → 连接 |
| **API 调用** | `POST https://abc-def-123.trycloudflare.com/api/chat` |

### 公网访问对比

| 特性 | 局域网 (WiFi/USB) | 公网 (Cloudflare Tunnel) |
|------|-------------------|--------------------------|
| 需要同一网络 | ✅ 是 | ❌ 不需要 |
| 协议 | `ws://` + `http://` | `wss://` + `https://` |
| 速度 | 最快（局域网直连） | 稍慢（经过 Cloudflare 中转） |
| 安全性 | 仅限本地 | HTTPS 加密 |
| 域名 | 需要知道 IP | 自动生成域名 |
| 费用 | 免费 | 免费 |
| 稳定性 | 稳定 | Quick Tunnel 每次重启 URL 变化 |

### Android App 设置说明

在 App 设置页面中：

1. **局域网连接（默认）**：填写电脑 IP 和端口，不需要开启公网地址
2. **公网连接**：
   - 开启「公网地址」开关
   - 在输入框中粘贴 Cloudflare Tunnel 的 URL（如 `https://abc-def-123.trycloudflare.com`）
   - 点击「连接」，App 会自动使用 WSS 协议连接

> 💡 公网和局域网配置可以同时保存，方便在不同网络环境下切换使用。

> ⚠️ Quick Tunnel 的 URL 每次重启 cloudflared 会变化。如需固定域名，可注册免费 Cloudflare 账号并使用命名隧道（Named Tunnel）。

## 防重复输出优化

针对小模型容易陷入重复循环的问题，系统实施了多层防重复策略：

### 1. 模型生成参数优化

| 参数 | 原值 | 新值 | 说明 |
|------|------|------|------|
| `repeat_penalty` | 1.3 | **1.8** | 大幅提高重复惩罚力度 |
| `repeat_last_n` | 64 | **256** | 扩大重复检测窗口到 256 token |
| `num_predict` | 256 | **150** | 限制最大生成 token 数 |
| `tfs_z` | - | **1.0** | Tail Free Sampling（仅 API 调用） |

以上参数同时在 `Modelfile` 和 `server/ollama_client.py` 中配置。修改 Modelfile 后需要重新创建模型：

```bash
ollama create elysia-lora -f Modelfile
```

### 2. System Prompt 反重复指令

在 `elysia_prompt.py` 的 System Prompt 中添加了【严格禁止重复】段落。

### 3. 实时重复检测与截断（后处理）

`ollama_client.py` 内置 5 层重复检测机制，每 30 个字符实时检测一次：

| 检测层 | 策略 | 触发条件 |
|--------|------|----------|
| 层1 | 连续相同句子 | 精确匹配 2 次 |
| 层2 | 短语级重复（2-8字） | 连续出现 3 次 |
| 层3 | 中等块重复（10-40字符） | 出现过即触发 |
| 层4 | 大段落重复（50-200字符） | 出现过即触发 |
| 层5 | 字符频率异常 | 某汉字占比 >15% |

检测到重复后自动截断到重复开始前的最后一个完整句子。

### 4. 硬字符限制（最后防线）

无论是否检测到重复，输出超过 **500 字符**时自动截断，防止极端情况下的超长重复输出。

### 5. 启动脚本自动清理端口

`start.bat` 会在启动服务前自动检测并杀掉占用 8080 端口的旧进程，避免端口冲突。

---

## 模型路由策略

服务支持三种模型来源模式（在 `config.py` 中配置 `MODEL_SOURCE`）：

| 模式 | 说明 |
|------|------|
| `ollama` | 仅使用本地 Ollama 模型 |
| `cloud` | 仅使用云端 API |
| `auto` | **默认**。优先 Ollama，不可用时自动切换到云端 API |

所有模型都会自动注入爱莉希雅的 System Prompt 和 Few-Shot 示例，确保回复风格一致。

## 爱莉希雅人设注入

`elysia_prompt.py` 中定义了完整的角色人设：

- **System Prompt**：包含性格特点、说话风格、经典台词、反重复指令
- **Few-Shot 示例**：5 组示例对话（打招呼/自我介绍/天气/安慰/讲笑话）
- **自动构建**：`build_messages()` 函数自动组装 System + 记忆 + Few-Shot + 历史 + 用户消息

## 情感识别引擎

`emotion_engine.py` 提供多维情感分析，使 AI 回复更具同理心。

### 核心功能

| 功能 | 说明 |
|------|------|
| **多维情感分析** | 识别喜悦/悲伤/愤怒/恐惧/惊讶/厌恶/中性 7 种情绪 |
| **情绪强度评分** | 对每种情绪给出 0-1 强度评分 |
| **情绪趋势追踪** | 追踪对话过程中的情绪变化趋势 |
| **自适应 Prompt** | 根据用户情绪状态自动调整回复策略 |

### 情感关键词库

引擎内置 30+ 行关键词库，通过规则匹配和加权计算实现实时情感识别，支持中文情绪表达。

## RAG 记忆系统

内置 RAG（Retrieval-Augmented Generation）记忆系统，让爱莉希雅能"记住"过去的对话。

### 记忆流程

```
用户消息 → 语义检索记忆 → 注入 Prompt → AI 回复 → 保存对话到记忆库
              │                                    │
              ▼                                    ▼
        ┌─────────────┐                    ┌─────────────┐
        │ 记忆数据库    │                    │ 自动提取     │
        │ (向量存储)    │                    │ 用户关键信息  │
        │             │                    │ (偏好/姓名等) │
        │ • 对话历史    │                    └─────────────┘
        │ • 用户事实    │
        │ • 对话摘要    │
        └─────────────┘
```

### 记忆后端选择

在 `config.py` 中配置 `MEMORY_BACKEND`：

| 后端 | 配置值 | 内存占用 | 搜索精度 | 依赖 |
|------|--------|----------|----------|------|
| **TF-IDF** (默认) | `"tfidf"` | ~5MB | 良好 | scikit-learn |
| **ChromaDB** | `"chromadb"` | ~400MB | 精准 | chromadb |

```python
# server/config.py
MEMORY_BACKEND = "tfidf"      # 轻量模式（推荐）
MEMORY_BACKEND = "chromadb"   # 精准语义搜索模式
```

## Web 管理面板

访问 `http://localhost:8080/dashboard` 使用完整的 Web 管理面板：

| 页面 | 功能 |
|------|------|
| 📊 仪表盘 | 实时统计、状态指示灯、实时日志流 |
| 💬 聊天测试 | 网页端直接测试聊天（SSE 流式输出） |
| 🖥️ Ollama 设置 | Ollama 状态检测、模型列表、配置 |
| ☁️ 云端 API | 提供商选择、API Key、参数调整、测试连接、模型路由 |
| ⚙️ 服务设置 | 监听地址/端口/连接数/心跳/超时、人设预览 |
| 📋 日志 | 系统日志浏览（支持级别过滤、实时推送） |
| 📱 连接设备 | 当前连接设备列表、连接历史日志 |

公网用户也可以通过 `https://xxx.trycloudflare.com/dashboard` 访问管理面板。

## 网络连接方式

| 方式 | 说明 | 适用场景 |
|------|------|----------|
| **WiFi（局域网）** | 手机和电脑在同一 WiFi，输入电脑 IP | 家庭/办公室 |
| **USB** | ADB 端口转发 `adb reverse tcp:8080 tcp:8080` | 开发调试 |
| **Cloudflare Tunnel** | 通过公网 HTTPS/WSS 访问 | 不在同一网络、远程访问 |

### USB 端口转发

```bash
# 电脑端执行
adb reverse tcp:8080 tcp:8080
# 手机端设置中选择 USB，IP 填 localhost
```

## 多电脑协同推理（分布式 Ollama）

如果你有多台电脑，可以将**推理和服务分开部署**，让有 GPU 的电脑专门跑模型，其他电脑负责服务调度。

### 架构示意

```
┌─────────────────┐         ┌─────────────────────┐
│  核显电脑 (主服务) │         │  RTX 4060 电脑 (推理)  │
│                 │  HTTP   │                     │
│  FastAPI 服务    │ ◄═════► │  Ollama 推理服务      │
│  SQLite 数据库   │  调用   │  (加载大模型)         │
│  RAG 记忆系统    │         │                     │
│  管理面板        │         │  只负责 GPU 推理      │
│  WebSocket 管理  │         │                     │
└─────────────────┘         └─────────────────────┘
         ▲
         │ WiFi/公网
         ▼
   ┌─────────────┐
   │  Android App │
   └─────────────┘
```

**核心原理**：Ollama 本身就是 HTTP API 服务（默认端口 11434），FastAPI 服务端只需将 `OLLAMA_BASE_URL` 改为独显电脑的局域网 IP。

### 配置步骤

#### 1. RTX 4060 电脑（推理节点）

```bash
# 允许局域网远程访问（必须设置）
set OLLAMA_HOST=0.0.0.0
ollama serve

# 拉取模型
ollama pull qwen2.5:14b
ollama pull qwen2.5:7b
ollama pull elysia-lora
```

#### 2. 核显电脑（服务节点）

修改 `server/config.py`：

```python
OLLAMA_BASE_URL = "http://192.168.1.100:11434"  # 替换为实际 IP
OLLAMA_MODEL = "qwen2.5:14b"
```

然后正常启动服务。

#### 3. 验证连接

```bash
curl http://192.168.1.100:11434/api/tags
curl http://localhost:8080/api/status
```

### 推荐模型配置

| GPU | 推荐模型 | 显存占用 | 说明 |
|-----|----------|----------|------|
| RTX 4060 (8GB) | qwen2.5:14b-q4 | ~9GB | 14B 量化版，质量好 |
| RTX 4060 (8GB) | qwen2.5:7b | ~4.7GB | 7B 原版，速度快 |
| RTX 4060 (8GB) | elysia-lora | 取决于基座 | 自定义微调模型 |
| RTX 4090 (24GB) | qwen2.5:32b | ~19GB | 32B 量化版 |
| 核显/无 GPU | 云端 API | 0GB | 走 DeepSeek/OpenAI |

### 三种部署模式对比

| 模式 | 场景 | 配置 |
|------|------|------|
| **单机部署** | 一台电脑既有 GPU 又跑服务 | 默认配置，`OLLAMA_BASE_URL=http://localhost:11434` |
| **分布式部署** | 独显跑推理，核显跑服务 | 修改 `OLLAMA_BASE_URL` 为独显电脑 IP |
| **混合模式** | 本地无 GPU，用云端 API 兜底 | `MODEL_SOURCE=auto`，自动在 Ollama 和云端之间切换 |

> 💡 分布式部署 + `MODEL_SOURCE=auto` 可以实现：优先用独显电脑的 Ollama，如果独显电脑离线则自动切换到云端 API，保证服务永不中断。

## API 端点

### 聊天与会话

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | 聊天（REST，支持流式 SSE） |
| WS | `/ws/chat` | 聊天（WebSocket 实时流式） |
| POST | `/api/chat/stop` | 停止当前生成 |
| POST | `/api/sessions` | 创建会话 |
| GET | `/api/sessions` | 会话列表 |
| GET | `/api/session/list` | 会话列表（详细） |
| GET | `/api/session/stats` | 会话统计 |
| DELETE | `/api/sessions/{id}` | 删除会话及所有消息 |
| PUT | `/api/sessions/{id}/close` | 关闭会话 |
| GET | `/api/messages/{id}` | 消息历史 |
| DELETE | `/api/messages/{id}` | 删除单条消息 |
| GET | `/api/messages/search?q=xxx` | 搜索消息内容 |

### 云端 API 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cloud/providers` | 获取支持的提供商列表 |
| GET | `/api/cloud/config` | 获取当前云端配置 |
| POST | `/api/cloud/config` | 更新云端配置（API Key 等） |
| POST | `/api/cloud/test` | 测试云端 API 连接 |

### 模型管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/model/check` | 检查 Ollama 模型状态 |
| POST | `/api/model/check` | 强制刷新模型状态 |
| GET | `/api/model/info` | 模型详细信息 |

### 记忆系统

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/memory/stats` | 记忆库统计（对话/事实/摘要数量） |
| GET | `/api/memory/search?q=关键词` | 语义搜索记忆 |
| POST | `/api/memory/fact` | 手动添加用户事实 |
| POST | `/api/memory/summary` | 添加对话摘要 |
| POST | `/api/memory/clear` | 清空记忆 |
| POST | `/api/memory/cleanup` | 手动触发记忆清理 |
| POST | `/api/memory/auto-cleanup` | 自动清理过期记忆 |
| GET | `/api/memory/export` | 导出记忆数据 |
| POST | `/api/memory/import` | 导入记忆数据 |

### 情绪分析

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/emotion/analyze` | 分析当前对话情感 |
| GET | `/api/emotion/trend/{session_id}` | 会话情感趋势 |
| POST | `/api/emotion/reset` | 重置情感追踪 |

### 用户学习

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/learning/profile` | 用户画像（基本信息/性格/风格/偏好/情感） |
| GET | `/api/learning/progress` | 学习进度（各维度完成度/置信度） |
| GET | `/api/learning/network` | 记忆神经网络统计（节点/边/密度/中心节点） |
| GET | `/api/learning/context?q=xxx` | 获取个性化上下文（调试用） |
| GET | `/api/learning/summary` | 用户摘要 |
| POST | `/api/learning/reset` | 重置用户学习数据 |

### 系统配置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 获取运行时配置 |
| PUT | `/api/config` | 更新运行时配置（持久化） |

### 系统监控

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 服务首页 |
| GET | `/health` | 健康检查（轻量级，供客户端探测） |
| GET | `/api/status` | 服务状态（Ollama/GPU/连接数） |
| GET | `/api/logs` | 系统日志 |
| GET | `/api/logs/connections` | 连接日志 |
| GET | `/dashboard` | Web 管理面板 |
| WS | `/ws/logs` | 实时日志流 |

## API 认证与安全

### Token 认证

服务支持可选的 API Token 认证，适合公网访问时保护 API 不被滥用：

```python
# server/config.py
API_TOKEN = ""           # 留空 = 不需要认证（局域网推荐）
API_TOKEN = "my-secret"  # 设置后需要在请求头中携带 Token
```

设置 Token 后，所有 API 请求需要在 Header 中携带：

```bash
curl -H "Authorization: Bearer my-secret" http://localhost:8080/api/status
```

> 💡 以下端点**不需要认证**：首页 `/`、健康检查 `/health`、管理面板 `/dashboard`、静态文件 `/static/*`、WebSocket `/ws/*`

### 速率限制

服务内置速率限制防止滥用：

| 接口 | 限制 |
|------|------|
| 通用 API | 120 次/分钟/IP |
| 聊天接口 `/api/chat` | 60 次/分钟/IP |

超过限制返回 `429 Too Many Requests`：

```json
{"detail": "请求过于频繁，请稍后再试", "retry_after": 60}
```

可在 `config.py` 中调整：

```python
RATE_LIMIT_PER_MINUTE = 120       # 通用限制（0 = 不限制）
CHAT_RATE_LIMIT_PER_MINUTE = 60   # 聊天接口限制
```

### 健康检查

`GET /health` 返回轻量级健康状态，适合负载均衡器或客户端探测：

```json
{
  "status": "ok",
  "timestamp": "2025-01-01T00:00:00",
  "ollama": true,
  "cloud_configured": false,
  "active_connections": 2
}
```

## Android 客户端功能

### 消息操作

长按任意消息气泡可弹出操作菜单：

| 操作 | 说明 |
|------|------|
| 📋 复制 | 复制消息文本到剪贴板 |
| 🔄 重新发送 | 重新发送用户消息（移除后续对话） |
| 🔄 重新生成 | 让 AI 重新生成回复 |

### 消息状态指示

用户消息旁显示发送状态图标：

| 图标 | 状态 | 说明 |
|------|------|------|
| ⏳ | 发送中 | 消息正在发送 |
| ✓ | 已发送 | 消息已发出 |
| ✓✓ | 已送达 | 服务器已接收 |
| ❌ | 发送失败 | 发送失败，可长按重试 |

### 断线自动重连

WebSocket 断开后自动重连，采用**指数退避**策略：

- 第 1 次：1 秒后重连
- 第 2 次：2 秒后重连
- 第 3 次：4 秒后重连
- 第 4 次：8 秒后重连
- 第 5 次：16 秒后重连
- 5 次失败后提示用户手动重连

## 树莓派客户端

树莓派客户端 (`rpi_client/`) 可作为独立终端连接 PC 服务端进行聊天：

| 文件 | 说明 |
|------|------|
| `elysia_client.py` | 主客户端，通过 WebSocket 连接服务端 |
| `elysia_cli.py` | 命令行交互工具，在终端直接与爱莉希雅对话 |
| `setup.py` | 安装脚本 |
| `install.sh` | 一键安装（Linux/Raspberry Pi） |
| `requirements.txt` | Python 依赖 |

```bash
# 树莓派上一键安装
cd rpi_client
bash install.sh
```

## 技术栈

| 组件 | 技术方案 |
|------|----------|
| PC 后端 | Python FastAPI + SQLite + WebSocket |
| 本地推理 | Ollama（GPU 加速） |
| 云端推理 | OpenAI 兼容协议（DeepSeek/OpenAI/通义千问/智谱/Kimi） |
| 人设注入 | System Prompt + Few-Shot 示例对话 |
| 情感引擎 | 规则匹配 + 多维加权情感分析 |
| 记忆系统 | RAG 双后端（TF-IDF / ChromaDB） |
| 公网隧道 | Cloudflare Tunnel（免费 HTTPS/WSS） |
| 安全 | CORS 中间件 + 可选 Token 认证 + 速率限制 |
| Android UI | Kotlin + Jetpack Compose + Material3 |
| Android 网络 | OkHttp WebSocket（WS/WSS 双协议 + 自动重连） |
| 消息协议 | JSON over WebSocket（实时流式输出） |
| 消息功能 | 复制/重发/重新生成/状态指示 |
| 用户学习 | 记忆神经网络 + LLM 辅助分析 + 多用户隔离 |
| 配置持久化 | JSON 文件运行时配置持久化 |
| 主题风格 | 爱莉希雅粉色系（深色/浅色双主题） |
| 数据持久化 | SharedPreferences（聊天记录/配置/主题） |

## 数据库设计

### PC 端（SQLite）

| 表名 | 说明 |
|------|------|
| `chat_sessions` | 聊天会话（设备、连接方式、消息数） |
| `chat_messages` | 聊天消息（角色、内容、token 数、响应时间） |
| `connection_logs` | 连接日志（设备、IP、事件） |
| `system_logs` | 系统日志（级别、分类、消息） |

### 记忆存储

| 后端 | 存储位置 | 数据格式 |
|------|----------|----------|
| TF-IDF | `server/data/memory_tfidf/*.json` | JSON 文件 |
| ChromaDB | `server/data/memory_db/` | 向量数据库 |

### 学习数据存储

| 文件 | 说明 |
|------|------|
| `server/data/user_learning/user_profile.json` | 用户画像（默认设备） |
| `server/data/user_learning/profile_{device_id}.json` | 多设备独立画像 |
| `server/data/runtime_config.json` | 运行时配置持久化 |

## 依赖环境

### PC 端
- Python 3.10+
- Ollama（本地模型，可选）
- 云端 API Key（可选）
- cloudflared（公网访问，已包含在 server 目录）
- 虚拟环境已配置（`server/venv/`）

### Python 依赖
```
fastapi, uvicorn, httpx, pydantic, websockets    # 核心服务
cryptography                                      # 加密支持
slowapi                                            # 速率限制
```

> 💡 可选记忆后端依赖：
> - `scikit-learn` + `scipy` + `numpy` — TF-IDF 记忆后端
> - `chromadb` — ChromaDB 向量记忆后端

### Android 端
- Android Studio Hedgehog+
- Android SDK 34
- Kotlin 1.9.22
- Jetpack Compose BOM 2024.01.00

## 用户学习引擎（记忆神经网络）

内置智能学习系统，AI 通过持续对话逐步了解用户，构建个性化记忆神经网络，实现越来越贴合用户的回复。

### 学习架构

```
用户消息 → 多维度分析 → 知识图谱更新 → 个性化 Prompt 注入
              │                │                    │
              ▼                ▼                    ▼
        ┌──────────┐   ┌──────────────┐   ┌──────────────┐
        │ 规则引擎   │   │ 记忆神经网络  │   │ 个性化回复    │
        │ (实时)    │   │ (加权图结构)  │   │ (Prompt增强)  │
        ├──────────┤   │              │   ├──────────────┤
        │ LLM 分析  │   │ 节点=知识    │   │ 根据用户性格  │
        │ (每10条)  │   │ 边=关联强度  │   │ 调整回复风格  │
        └──────────┘   └──────────────┘   └──────────────┘
```

### 六大核心能力

| 能力 | 说明 |
|------|------|
| **用户画像构建** | 自动提取名字、职业、地点等基本信息，分析五大人格特质 |
| **情感分析** | 实时识别积极/消极/兴奋情绪，追踪情感趋势 |
| **聊天习惯学习** | 分析活跃时间、话题偏好、沟通风格（正式/随意/emoji使用） |
| **记忆神经网络** | 带权重的知识图谱，节点间相互关联，自动衰减旧记忆 |
| **LLM 深度分析** | 每 10 条消息调用 LLM 做深度用户理解（性格/兴趣/目标） |
| **反馈修正循环** | 用户纠正信息时自动修正知识图谱（"不是XX，是YY"） |

### 知识图谱结构

- **节点** = 知识点（偏好、目标、观点、习惯、社交关系等 10 大类）
- **边** = 关联强度（同时出现的知识节点自动建立连接）
- **权重** = 重要程度（0.05~1.0，高频访问增强，长期未访问衰减）
- **记忆衰减**：30 天未访问权重 ×0.5，90 天 ×0.1，最低 0.05（永不完全遗忘）

### 个性化注入

每次 AI 回复时，自动将学习到的用户画像注入 System Prompt：
- 用户基本信息（名字、职业、地点）
- 性格特点（外向/内向、开放/保守）
- 沟通风格偏好（正式/随意、简洁/详细）
- 兴趣话题（按频次排序的 Top 5）
- 情感状态（近期主导情绪）
- 知识图谱中的关键节点（与当前话题相关的高权重知识）

## 配置持久化

服务端运行时配置变更自动保存到 `server/data/runtime_config.json`，服务重启后自动恢复。

### 持久化配置项

| 配置 | 说明 |
|------|------|
| `cloud_provider` | 云端 API 提供商 |
| `cloud_api_key` | API Key |
| `model_source` | 模型路由模式 |
| `memory_backend` | 记忆后端选择 |
| `rate_limit_per_minute` | 速率限制 |

## Android 客户端改进

### 数据持久化

- **聊天记录持久化**：通过 SharedPreferences 保存最多 500 条消息，App 重启不丢失
- **服务器配置持久化**：IP、端口、公网地址等配置自动保存，下次打开自动恢复
- **自动连接**：开启后 App 启动时自动连接上次的服务器

### 主题切换

- 支持**深色/浅色主题**切换，顶部栏一键切换 🌙/☀️
- 爱莉希雅粉色系配色，深色和浅色两种方案
- 主题偏好自动持久化

## 快速命令参考

```bash
# 启动服务
cd server && start.bat

# 启动公网隧道（另一个终端）
cd server && start_tunnel.bat

# 配置云端 API
curl -X POST http://localhost:8080/api/cloud/config \
  -H "Content-Type: application/json" \
  -d '{"provider":"deepseek","api_key":"sk-your-key"}'

# 查看服务状态
curl http://localhost:8080/api/status

# 健康检查
curl http://localhost:8080/health

# 查看记忆统计
curl http://localhost:8080/api/memory/stats

# 带 Token 认证的请求
curl -H "Authorization: Bearer my-secret" http://localhost:8080/api/status