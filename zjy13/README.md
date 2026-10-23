# AI-Role · 云端 LLM 模块（第五阶段切片）

> 本目录当前**只实现了方案文档第五节的云端 LLM 模块**，其余模块（ASR / TTS / WebSocket / 固件）尚未开始。
> 所有测试结果均为本机真实执行记录，未在真实云端服务商上验证的部分已明确标注。

## 1. 本阶段交付范围

| 文档要求（第五节） | 实现位置 | 状态 |
| --- | --- | --- |
| 1. 地址/模型/密钥来自环境变量或本地配置 | `server/config.py` `LLMConfig.from_env()` | ✅ 已验证 |
| 2. 支持角色卡注入 | `server/dialogue/character.py` | ✅ 已验证 |
| 3. 支持最近对话历史 | `build_messages(max_history_messages=...)` | ✅ 已验证 |
| 4. 支持多轮对话 | 无状态 messages 列表，由调用方维护 | ✅ 已验证 |
| 5. 优先支持流式文本输出 | `LLMClient.chat(on_delta=...)` | ✅ 已验证（对本地桩） |
| 6. 记录首片段与完整回复时间 | `LLMResult.first_token_ms` / `total_ms` | ✅ 已验证 |
| 7. 超时/限流/网络/API 错误处理 | `server/llm/base.py` 错误体系 | ✅ 已验证 |
| 8. 日志不输出密钥 | `redacted()` + 测试断言 | ✅ 已验证 |
| 9. 角色卡/历史/系统提示词不混为硬编码 | `SYSTEM_PROMPT_TEMPLATE` + `build_messages` | ✅ 已验证 |
| 10. 支持取消当前轮次 | `LLMClient.cancel()` | ✅ 已验证 |
| 11. 有限次重试 + 退避 | `_chat_with_retry` / `_backoff_delay` | ✅ 已验证 |
| 12. 防同轮重复调用（避免重复扣费） | `turn_id` 去重 | ✅ 已验证 |
| 13. 服务商返回格式适配 | `ChatAdapter` 注册表 | ✅ 已验证（OpenAI 兼容协议） |

**未实现（按文档要求延后）**：真实服务商联调、TTS 音色绑定、角色切换、记忆系统、WebSocket 协议。

## 2. 环境（本机实测）

| 项 | 值 |
| --- | --- |
| 操作系统 | Windows |
| Python | 3.11.9（`C:\Users\user\AppData\Local\Programs\Python\Python311`） |
| 虚拟环境 | `D:\xiangmudaima\zjy13\.venv` |
| GPU | NVIDIA GeForce RTX 5070，12227 MiB |
| 驱动 / CUDA UMD | 610.62 / 13.3 |
| 依赖 | httpx 0.28.1、PyYAML 6.0.3、python-dotenv 1.2.4、pytest 9.1.1 |

> 本阶段不涉及 GPU；ASR 阶段的 PyTorch/CUDA/FunASR 版本需在阶段一单独核实，**不在此处假设**。

### 网络说明（实测）

- `pypi.org`、`pypi.tuna.tsinghua.edu.cn` 的 **HTTPS 握手失败**（TCP 443 可达，TLS 层被拦截）。
- 使用 `-i https://pypi.tuna.tsinghua.edu.cn/simple` 配合 `--timeout 20` 时依赖安装成功（pip 重试后连通）。
- 若你所在网络同样受限，建议直接复用已建好的 `.venv`，或改用可用的镜像/代理。

## 3. 安装与运行

```powershell
cd D:\xiangmudaima\zjy13
# 虚拟环境已创建；如需重建：
# python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 配置

```powershell
Copy-Item .env.example .env
notepad .env   # 填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
```

`.env` 已在 `.gitignore` 中，不会被提交。

### 冒烟测试（真实服务商）

```powershell
$env:PYTHONUTF8="1"
.\.venv\Scripts\python.exe -m tools.llm_smoke "你好，介绍一下你自己"
```

预期：打印脱敏配置、角色卡名、流式回复，以及 `first_token_ms` / `total_ms` / `usage`。

### 冒烟测试（无密钥，本地桩）

```powershell
# 终端 A
.\.venv\Scripts\python.exe -m tools.fake_llm_server --port 8765

# 终端 B
$env:LLM_BASE_URL="http://127.0.0.1:8765/v1"
$env:LLM_API_KEY="local-test-key"
$env:LLM_MODEL="fake-model"
.\.venv\Scripts\python.exe -m tools.llm_smoke "你好"
```

> ⚠️ 本地桩返回固定文本，**只能验证客户端代码路径，不能代表真实模型质量或真实延迟**。

### 单元测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

本机实测结果：**51 passed in 10.23s**。

## 4. 真实测试记录

### 4.1 自动化测试（2026-10-23 本机）

```
$ .\.venv\Scripts\python.exe -m pytest tests -q
..........................................................               [100%]
58 passed in 10.28s
```

覆盖：配置解析/校验/脱敏、SSE 解析、HTTP 状态码映射、重试后成功、鉴权失败不重试、
限流、5xx、坏 JSON、连接拒绝、超时、turn 去重、cancel 中途取消、角色卡加载、
历史截断、system 注入防护、密钥不落日志、思考内容不进正文、thinking 参数构造、
.env 编码损坏检测。

### 4.2 真实 DeepSeek 调用（2026-10-23，本机）

```
$ .\.venv\Scripts\python.exe -m tools.llm_smoke "今天有点累，不想动了。"
model: deepseek-flash    thinking: disabled
回复: 嗯，累的时候就别硬撑了。你是刚忙完什么，还是单纯一天下来没劲儿了？
first_token_ms: 705.7   total_ms: 986.3   delta_count: 23   attempts: 1
usage: prompt=454 completion=23 total=477
```

命中 prompt 缓存时（系统提示词固定，第二次起稳定命中约 256 token）：

```
回复: 嗯，这个不行。我不知道你们昨天开会说了什么，编出来的记录是假的……
first_token_ms: 417.2   total_ms: 797.9
usage: prompt=458  cache_hit=256  cache_miss=202
```

15 次真实请求统计（默认角色卡，thinking=disabled）：

| 指标 | deepseek-flash | deepseek-v4-pro |
| --- | --- | --- |
| 首字延迟 mean | 646 ms | 877 ms |
| 首字延迟 P95 | 984 ms | 1153 ms |
| 总耗时 mean | 996 ms | 1202 ms |
| 总耗时 P95 | 1274 ms | 1595 ms |

完整选型分析见 [docs/model-selection.md](docs/model-selection.md)。

## 5. 配置状态

**已定稿（2026-10-23，用户确认）**

- 模型：`deepseek-flash`
- 思考：`LLM_THINKING=disabled`
- 不做双模型路由
- 超时/重试/输出长度参数由实现方按实测数据确定，见下表

| 参数 | 值 | 依据 |
| --- | --- | --- |
| `LLM_REQUEST_TIMEOUT_S` | 20 | 实测 P95 总耗时 1.3s |
| `LLM_TOTAL_TIMEOUT_S` | 45 | 语音场景超过 45s 无交互意义 |
| `LLM_MAX_ATTEMPTS` | 3 | 含首次，累计退避约 1.5s |
| `LLM_BACKOFF_MAX_S` | 4 | 退避上限 |
| `LLM_MAX_TOKENS` | 256 | 约 170 汉字，够 1~5 句且拦住长篇 |

⚠️ 该密钥曾以明文形式出现在对话记录中，**建议到 DeepSeek 控制台轮换**后更新 `.env`。

仍需你确认：

1. 该账号的并发/限流配额（若触发 429 需调整 `LLM_MAX_ATTEMPTS` 与退避参数）。

## 6. 常见问题排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `[配置缺失] 未设置: LLM_BASE_URL, ...` | 未创建 `.env` 或变量名为空 | 复制 `.env.example` 为 `.env` 并填写 |
| `[config_error] LLM_BASE_URL 必须以 http:// 或 https:// 开头` | 漏写协议头 | 补全 `https://` |
| `[auth_error] invalid api key` | 密钥错误/过期/区域不符 | 核对密钥；此类错误**不会重试** |
| `[rate_limited]` | 触发服务商限流 | 调大 `LLM_BACKOFF_BASE_S`，或降低并发 |
| `[timeout]` | 网络慢或 `LLM_REQUEST_TIMEOUT_S` 过小 | 提高超时；确认 `LLM_TOTAL_TIMEOUT_S >= LLM_REQUEST_TIMEOUT_S` |
| `[protocol_error] 流式数据不是合法 JSON` | 服务商不是 OpenAI 兼容协议 | 需实现新的 `ChatAdapter` 子类并 `register_adapter` |
| `[duplicate_turn]` | 同一 `turn_id` 重复调用 | 正常保护；确需重试先 `client.reset_turn(turn_id)` |
| 中文输出乱码 | Windows 控制台 GBK | 设 `$env:PYTHONUTF8="1"` |
| `[config_error] 配置文件 ... 不是合法的 UTF-8 编码` | 用 PowerShell `Add-Content` 或记事本按 GBK 保存了 `.env` | 用 VS Code 或记事本「另存为」UTF-8 重存 |
| `pip` 安装超时 | 网络对 PyPI TLS 拦截 | 使用国内镜像并加 `--timeout 20` |
| PowerShell 显示 `[exit code: 1]` 但脚本实际成功 | PowerShell 把 stderr 日志当错误 | 用 `$LASTEXITCODE` 判断真实退出码 |

## 7. 目录结构（当前实际）

```
zjy13/
├─ .env.example          # 配置模板（不含真实密钥）
├─ .gitignore
├─ requirements.txt
├─ conftest.py           # pytest 路径配置
├─ server/
│  ├─ __init__.py
│  ├─ config.py          # LLMConfig：环境变量读取/校验/脱敏
│  ├─ llm/
│  │  ├─ __init__.py     # 对外导出
│  │  ├─ base.py         # ChatMessage / LLMResult / 错误体系
│  │  ├─ adapter.py      # ChatAdapter 注册表 + OpenAI 兼容实现
│  │  └─ client.py       # LLMClient：流式/重试/取消/去重/计时
│  ├─ dialogue/
│  │  ├─ __init__.py
│  │  └─ character.py    # 角色卡加载 + 提示词结构化组装
│  └─ characters/
│     └─ default.yaml    # 默认角色卡（含 5 组示例对话）
├─ tools/
│  ├─ __init__.py
│  ├─ fake_llm_server.py # 本地 OpenAI 兼容桩（仅测试用）
│  └─ llm_smoke.py       # 命令行冒烟
└─ tests/
   ├─ test_config.py
   ├─ test_adapter.py
   ├─ test_character.py
   └─ test_client.py
```

## 8. 下一阶段前置条件

1. 提供 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`，跑通真实服务商冒烟，记录真实首 token 延迟。
2. 确认服务商协议细节；若为 OpenAI 兼容则无需改代码。
3. 之后才进入阶段一（本地 ASR）或云端 TTS 模块——**按文档要求，不要在 ASR 未通过时提前生成固件**。

## 9. 安全说明

- 密钥只从环境变量/`.env` 读取，代码中无硬编码；`.env` 已忽略。
- 日志只记录 `api_key_set: True/False`，不记录密钥值（有测试断言）。
- 角色卡与用户输入均作为数据进入 `user` 消息，历史中的 `system` 会被丢弃，防止覆盖服务端安全规则。
- `httpx` 使用 `trust_env=False`，避免系统代理把密钥意外转发到未知节点。
