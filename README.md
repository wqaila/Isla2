# 赛博玩具 AI · 次元萌盒 —— 项目工作区

一个围绕「二次元 AI 陪伴」展开的个人项目集合，从产品方案一路做到可运行的本地 LLM 聊天系统。

> 本仓库用分支管理不同阶段的工作：`master` 是当前的工作区快照（含全部 6 个子项目），
> 历史上还有 `tiqutaici`（台词提取）、`main` 等分支。

---

## 目录一览

| 目录 | 项目 | 技术栈 | 状态 |
|------|------|--------|------|
| [`zjy2/`](zjy2/) | **次元萌盒** —— 桌面级 AI 互动硬件原型 | 产品文档 + 示例代码 | 方案设计阶段，未实现 |
| [`zjy5/`](zjy5/) | **B站下载器 + 台词 OCR** | Python / yt-dlp / PaddleOCR | 可用 |
| [`zjy6/`](zjy6/) | **Qwen/Gemma 多端本地部署** | Android(Kotlin+JNI) / llama.cpp | Android 端需自行构建 |
| [`zjy7/`](zjy7/) | **爱莉希雅 LoRA 微调** | transformers / peft / Qwen2.5-7B | 流水线完整 |
| [`zjy8/`](zjy8/) | **GGUF 修复 + 本地推理服务** | Python / llama-server | 可用 |
| [`zjy9/`](zjy9/) | **爱莉希雅 AI 聊天系统** | FastAPI + Android + 树莓派 + 网页 | 功能最完整 |

另外根目录还有一份 [`代码审查报告.md`](代码审查报告.md)，
记录了对全部 6 个项目做代码审查时发现的 70 项问题及修复建议。

---

## 各项目速览

### zjy9 —— 爱莉希雅 AI 聊天系统（主力项目）

基于本地大语言模型 + 云端 API 的角色扮演聊天系统：

- **PC 端服务**：FastAPI，提供 REST + WebSocket + 管理面板
- **模型来源**：本地 Ollama 优先，不可用时自动切到云端（DeepSeek / OpenAI / 通义 / 智谱 / Kimi）
- **记忆系统**：RAG 双后端（TF-IDF 轻量 / ChromaDB 精准），自动提取用户事实
- **用户学习**：知识图谱 + 画像演化，按设备隔离
- **情感引擎**：7 维情绪识别 + 趋势追踪，动态调整回复语气
- **客户端**：Android（Kotlin + Compose）、树莓派 CLI、Web 管理面板

```bash
cd zjy9/server
pip install -r requirements.txt
python main.py          # 或 start.bat
# 管理面板 http://localhost:8080/dashboard
```

### zjy7 —— LoRA 微调流水线

```bash
cd zjy7
pip install -r requirements.txt
python prepare_data.py       # 生成训练数据
python train_lora.py         # LoRA 训练
python post_train.py --quant Q8_0   # 合并 → GGUF → 量化 → 测试
python evaluate.py           # 独立评估集评测
```

### zjy5 —— B站下载器

```bash
cd zjy5/bilibili_downloader
python -m bilibili_downloader --help
```

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

**2026-09-21** —— 完成全工作区代码审查与集中修复：

- 修复 70 项问题（高危 18 / 中危 30 / 低危 22），详见 [`代码审查报告.md`](代码审查报告.md)
- zjy9：修复认证绕过、对话历史取反、路由遮蔽、记忆清理失效、上下文溢出、流式重复输出、多用户画像串号等 39 项
- zjy5：打通 CLI 入口、实现 WBI 签名、修复画质参数与字幕 cid、恢复 TLS 校验
- zjy6：修复 Android 权限申请、prompt 重复、流式不显示、token 缓冲写死等问题
- zjy7：重建独立评估集、修复路径硬编码、补随机种子、修复 Modelfile 缺 TEMPLATE
- zjy8：厘清三个互相矛盾的 GGUF 补丁脚本（**原 `fix_gguf.py` 会写坏模型文件**）、补 requirements、默认监听回环地址
- zjy2：删除空文件、修复跑不起来的单元测试
