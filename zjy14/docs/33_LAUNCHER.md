# 语音链路一键启动控制台

> **一句话**：在浏览器里点一个按钮，把 **ASR + LLM + TTS + 网关** 全部拉起来。
> TTS 用的是 **zjy14 的 CosyVoice3**（:50001），不是 zjy13 自带的 `tts_server_wsl`。

---

## 一、为什么需要它

改造前，让板子能说话要手工做四件事，且顺序不能错：

```bash
# 1) TTS（WSL，首次加载模型 10~15 秒）
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/start.sh cosyvoice3

# 2) ASR（Windows）—— 原本内嵌在网关里，网关启动时才加载
# 3) LLM —— 云端，只需 .env 里有 key
# 4) 网关（Windows，必须等 TTS 就绪否则直接退出码 2）
cd zjy13 && .venv-asr\Scripts\python.exe -m server.ws.server --host 0.0.0.0
```

问题：

| 痛点 | 后果 |
|---|---|
| 要开 2~3 个终端 | 顺序错了网关直接退出 |
| TTS 加载 10 秒以上 | 不知道「还在加载」还是「已经崩了」 |
| ASR 内嵌在网关里 | ASR 出问题要重启网关 → 板子连接全断 |
| TTS 容易指错 | `.env` 里 `TTS_BASE_URL=:50000` 是 **zjy13 的** TTS，不是 zjy14 的 |

改造后：一个网页，四个服务的状态、端口、PID、日志都在眼前，
启动/停止各自独立，**TTS 归属被显式钉死**在 zjy14。

---

## 二、三条使用路径

### 路径 A：网页一键启动（最省事，推荐）

```powershell
cd D:\xiangmudaima\zjy14
.\scripts\start_web.ps1 -OpenBrowser
```

或直接双击 `一键启动网页.bat`。这一步会：

1. 起启动器（:50100，服务编排）
2. 起前端 dev 服务器（Vite，:5173）
3. 打开 **`http://127.0.0.1:5173/services`**

然后在页面上点 **「一键启动全部」** 拉起 ASR/LLM/TTS/网关。
想连这一步也自动化：加 `-WithServices`。停止网页与启动器：`-Stop`。

> **为什么网页能冷启动**：本页是静态前端（Vite），启动器是独立进程；
> 两者都**不依赖** zjy14 后端 :50001 —— 所以 TTS 还没起来时本页照样能打开操作。

### 路径 B：独立控制台（兜底，不依赖 node）

```powershell
.\scripts\start_launcher.ps1 -OpenBrowser
```

浏览器打开 `http://127.0.0.1:50100/`，点 **「一键启动全部」**。

> 它是一张**自包含的 HTML**（`launcher/static/index.html`），
> 只有 Python 标准库就能跑 —— 当 node/Vite 坏掉时用它救场。
> 与路径 A 共用**同一套**启动器 API，不存在两套启动逻辑。

### 路径 C：手工启动（排查时用）

```bash
# 启动服务
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/run_server.sh
```

### 三条路径的关系

| 路径 | 需要 node | 需要 zjy14 后端 | 能力 |
|---|:-:|:-:|---|
| A 网页一键启动 | ✅ | ❌ | 完整（卡片/进度/日志/LLM 测试） |
| B 独立控制台 | ❌ | ❌ | 完整（同上，样式更朴素） |
| C 手工 | ❌ | — | 需自己记命令与顺序 |

**A 与 B 是同一套 API 的两个前端**：`launcher/server.py` 提供端点，
两个页面只负责展示。改启动逻辑只需改一处。

---

## 三、架构：三个进程 + 一个云端

```
                    ┌──────────────────────────────┐
   浏览器 ──────────▶│  启动器 :50100（Windows）     │
   :50100 或 :5173  │  launcher/server.py          │
                    │  仅标准库 · 只监听 127.0.0.1  │
                    └───────────┬──────────────────┘
                                │ 拉起 / 停止 / 探活
        ┌───────────────────────┼────────────────────────┐
        ▼                       ▼                        ▼
 ┌─────────────┐        ┌─────────────┐         ┌──────────────┐
 │ ASR :50002  │        │ 网关 :8765  │         │ TTS :50001   │
 │ Windows     │◀──HTTP─│ Windows     │──HTTP──▶│ WSL          │
 │ .venv-asr   │        │ .venv-asr   │         │ cosyvoice-venv│
 └─────────────┘        └──────┬──────┘         └──────────────┘
                               │ HTTPS
                               ▼
                        DeepSeek 云端（+可选本地代理 7897）
```

### 3.1 TTS 归属（需求重点）

网关启动时**显式注入**两个环境变量：

```python
env["TTS_BASE_URL"]    = "http://127.0.0.1:50001"   # zjy14 的 CosyVoice3
env["ASR_SERVICE_URL"] = "http://127.0.0.1:50002"   # 独立 ASR 服务
```

为什么这样能生效：zjy13 的 `load_env_file()` 用的是
`load_dotenv(override=False)` —— **进程环境变量优先于 `.env`**。
所以即使 `zjy13/.env` 里写着 `TTS_BASE_URL=:50000`，网关仍会走 :50001。

> **实测证据**：跑通一轮对话后，查询 zjy14 的合成历史
> `GET :50001/api/v1/history` 出现了对应记录
> （`engine=cosyvoice3, voice=elysia, dur=18.44s`），
> 与那一轮下发的 19.13s 音频吻合 —— 证明网关确实调的 zjy14。

### 3.2 ASR 独立化（本次新增能力）

改造前 ASR 内嵌在网关（`server/ws/server.py` 启动时 `ASREngine().load()`）。
本次新增 `zjy13/server/asr_service.py`，网关通过 `--asr-url` 转过去：

| 收益 | 说明 |
|---|---|
| 不重复占显存 | 网关带 `--asr-url` 时**不加载** FunASR，模型只驻留一份 |
| 可独立重启 | ASR 崩了不用断板子连接 |
| 可独立验证 | `POST :50002/transcribe` 直接测识别，不用起整套 |

为什么用标准库而不是 FastAPI：`.venv-asr` 里**没有** fastapi/uvicorn
（实测 `find_spec` 均为 False），装它们只是徒增依赖面。

---

## 四、端点

### 启动器（:50100）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 控制台页面（自带，单文件无构建） |
| GET | `/api/status` | 全部服务状态（running / healthy / pid / 端口 / detail） |
| GET | `/api/specs` | 服务定义（前端渲染用） |
| POST | `/api/start/<name>` | 启动单个（`{"engine":"cosyvoice3"}`） |
| POST | `/api/stop/<name>` | 停止单个 |
| POST | `/api/start-all` | 一键启动（按依赖顺序串行等待） |
| POST | `/api/stop-all` | 一键停止（倒序） |
| GET | `/api/log/<name>?lines=N` | 日志尾部 |
| GET | `/api/llm/env` | 只读配置检查（不联网） |
| POST | `/api/llm/test` | 真实连通性（先代理、失败回退直连） |

### ASR 独立服务（:50002）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 引擎/设备/模型/加载耗时 |
| POST | `/transcribe` | body=WAV 字节（`audio/wav`）或 `{"audio_path":...}` |

```bash
curl -X POST --data-binary @some.wav -H "Content-Type: audio/wav" \
     http://127.0.0.1:50002/transcribe
# → {"ok":true,"text":"...","inference_ms":104,"realtime_factor":0.019,...}
```

---

## 五、编排规则

`start_all` 的顺序与判据（**按依赖判定，不搞全局短路**）：

| 服务 | 依赖 | 必需 | 就绪判据 |
|---|---|:-:|---|
| `tts` | — | ✅ | `:50001/health` 返回 200 且 `ok=true` |
| `asr` | — | ✅ | `:50002/health` 且 `loaded=true` |
| `llm` | — | ❌ | `.env` 关键字段齐全（代理不通只警告） |
| `gateway` | tts, asr | ✅ | `:8765/api/devices/health` |

设计要点：

1. **失败不连坐**：`tts` 失败只让 `gateway` 变 `blocked`；
   `llm` 探测照常执行（它是无副作用的只读检查）。
   早期实现用一个全局 `failed` 短路，会把 LLM 也标成「skipped」。
2. **`required=False` 不参与总判定**：LLM 是云端，代理没开也不该
   让「整条链路不可用」—— 那会把「代理没开」误报成「服务坏了」。
3. **快速失败**：启动后 6 秒内进程退出且端口未监听 → 立刻把日志尾巴
   回给前端，而不是让用户干等 `ready_timeout`（TTS 是 180 秒）。

---

## 六、已知环境限制（重要，实测）

### 6.0 `.ps1` 必须带 UTF-8 BOM（实测踩过）

PowerShell 5.1 读 `.ps1`：**有 BOM 按 UTF-8**，**无 BOM 按系统 ANSI（GBK）**。
不含 BOM 的脚本里中文注释会被按 GBK 误解，字节序列凑出多余的括号，导致：

```
Unexpected token '}' in expression or statement.
Missing closing '}' in statement block or type definition.
```

整段脚本根本不执行。新写的 `start_web.ps1` / `start_launcher.ps1` /
`stop_tts.ps1` 一开始都没 BOM，全部踩中。

> 项目里已有的 `start.ps1` / `start_tts_detached.ps1` 都是 **UTF-8 + BOM**，
> 这就是原因。新增脚本请照此保存；`tests/test_web_integration.py`
> 里有断言会检查 BOM。

### 6.0b 语法错误让所有按钮静默失效（已修）

上线当晚用户反馈「点击一键启动没有用」。查下来不是启动逻辑的问题，
而是**控制台页面的内联 JS 有语法错误**：

```js
document.addEventListener("click", (e) => {   // 非 async
  ...
  const d = await getJSON(`/api/log/${name}?lines=80`);   // ← SyntaxError
});
```

浏览器**解析整段脚本失败** → 一个事件处理器都没注册 →
页面照常打开、按钮全部点不动、屏幕上没有任何提示。
`node --check` 一跑就报：

```
SyntaxError: await is only valid in async functions
```

修法三件事：

1. 把含 `await` 的处理抽成 `async function loadLog()`，回调里不再直接写 `await`；
2. **页面拆成「引导脚本 + 主脚本」两段**：引导脚本先装 `window.onerror`，
   并在 1 秒后检查 `window.__consoleReady`；主脚本挂了就在页面顶部亮红条，
   而不是让按钮静默失效；
3. 加回归测试 `tests/test_launcher_frontend.py`：用 `node --check` 校验内联 JS
   （WSL 里自动走 `node.exe` interop），外加「两段结构 + `__consoleReady` 必须存在」的结构断言。

> 教训：**前端脚本的失效是静默的**。后端有日志、有 HTTP 状态码，
> 而一行 JS 语法错误能让整个页面变成「看起来正常但什么都不能点」。
> 因此必须有「页面自检」+「语法校验」两道兜底。

### 6.1 受限环境里 `wsl.exe` 可能被拒（`Wsl/E_ACCESSDENIED`）

实测：**被沙箱/作业对象限制的非交互 Python 进程**里，
`wsl.exe --version` 能跑，但任何涉及发行版的操作都返回：

```
拒绝访问。
错误代码: Wsl/E_ACCESSDENIED
```

这不是配置问题。表现为控制台里 TTS 启动失败，并给出提示：

> WSL 拒绝访问：当前进程无法启动 WSL 发行版。请在你自己的 PowerShell
> 窗口里执行一次 `wsl -d Ubuntu-24.04 -e bash .../scripts/start.sh cosyvoice3`，
> 再回到本控制台刷新状态即可接管。

**在你自己的终端里运行 `start_launcher.ps1` 不会有这个问题**
（普通交互会话没有限制）。即使 TTS 是你手动起的，控制台也能
**接管**它 —— 状态判定同时看「本启动器拉起的进程」和「端口是否监听」。

### 6.2 停止 WSL 服务需要 PowerShell

Windows 上看到的 `:50001` 监听 PID 是 **WSL 端口转发进程**，
对它 `taskkill` 不会停掉里面的 uvicorn。所以提供：

```powershell
.\scripts\stop_tts.ps1
```

（内部执行 `wsl -d Ubuntu-24.04 -e bash -lc "pkill -f custom_voice.server"`）

### 6.3 编码

`wsl.exe` 默认按 **UTF-16LE** 输出，日志文件里会全是 `\x00` 间隔的乱码。
启动器做两件事：子进程注入 `WSL_UTF8=1`；读日志时按 BOM/零字节嗅探解码。

---

## 七、验收步骤

```powershell
# 1) 冷启动控制台
cd D:\xiangmudaima\zjy14
.\scripts\start_launcher.ps1 -OpenBrowser

# 2) 页面点「一键启动全部」（首次约 20~60 秒，TTS 要加载模型）
#    期望：tts / asr / llm / gateway 四项全部变绿，顶部显示「全链路就绪」

# 3) 验证 TTS 确实来自 zjy14
curl http://127.0.0.1:50001/health      # engine=cosyvoice3
curl http://127.0.0.1:8765/api/devices/health

# 4) 端到端一轮（无需板子）
cd D:\xiangmudaima\zjy13
.\.venv-asr\Scripts\python.exe -m tools.ws_client_sim --rounds 1

# 5) 确认这一轮被记在 zjy14 的库里（证明走的是 zjy14 TTS）
curl "http://127.0.0.1:50001/api/v1/history?limit=1"
```

实测记录（2026-11-26）：按上述步骤得到

```
start-all: ok=True
  tts      skip    已在运行且健康
  asr      started 已启动，正在等待就绪
  llm      probe   deepseek-flash @ api.deepseek.com（代理未监听，回退直连）
  gateway  started 已启动，正在等待就绪

一轮对话: ASR 101ms → LLM 3542ms → 音频 19.13s → playback_done
zjy14 历史: engine=cosyvoice3 voice=elysia dur=18.44s   ← TTS 归属证明
```

---

## 八、测试

```bash
cd zjy14 && source ~/cosyvoice-venv/bin/activate
PYTHONPATH=. python -m pytest tests/test_launcher.py tests/test_launcher_frontend.py \
                                   tests/test_web_integration.py -v
```

- `test_launcher.py`（30 项）：编排逻辑，全部替身，**不 spawn、不联网**。
- `test_launcher_frontend.py`（8 项）：独立控制台页面结构与 JSON 语法。
- `test_web_integration.py`（17 项）：**整合页能力对齐**（进度面板、
  四个操作入口、离线可复制命令、轮询不被 busy 暂停）、Vite 绑 127.0.0.1
  与代理 rewrite、以及 `.ps1` 的 UTF-8 BOM。

---

## 九、文件清单

| 文件 | 作用 |
|---|---|
| `launcher/orchestrator.py` | 服务定义 + 启动/停止/探活 + 编排 |
| `launcher/server.py` | 标准库 HTTP 服务（API + 静态页） |
| `launcher/static/index.html` | 自包含控制台页面（兜底，无 node 也能用） |
| `launcher/__init__.py` | 包导出 |
| `scripts/start_web.ps1` | **网页一键启动**：启动器 + Vite + 打开页面（`-WithServices` 连链路一起起） |
| `scripts/start_launcher.ps1` | 只起独立控制台 |
| `scripts/stop_tts.ps1` | 单独停止 WSL 内的 TTS |
| `一键启动网页.bat` | 双击入口 → `start_web.ps1` |
| `一键启动控制台.bat` | 双击入口 → `start_launcher.ps1` |
| `web/src/api/launcher.ts` | 前端 API 客户端（离线不抛异常） |
| `web/src/pages/ServicesPage.tsx` | **整合版**「语音链路启动」页（进度/详细状态/日志/LLM 测试） |
| `web/src/styles-services.css` | 该页专用样式（进度条、键值对、日志块） |
| `web/vite.config.ts` | 绑 `127.0.0.1` + 代理 `/launcher/*` → `:50100/*` |
| `zjy13/server/asr_service.py` | 独立 ASR HTTP 服务（新增） |
| `zjy13/server/ws/server.py` | 新增 `--asr-url` / `ASR_SERVICE_URL` 远端 ASR |
| `tests/test_launcher.py` | 30 项：编排逻辑（替身，不 spawn） |
| `tests/test_launcher_frontend.py` | 8 项：控制台页面结构 + JS 语法 |
| `tests/test_web_integration.py` | 17 项：整合页能力对齐、Vite 绑定/代理、ps1 BOM |

---

## 十、安全边界

控制台能**启动本机任意进程**，所以：

- 默认只监听 `127.0.0.1`，不暴露到局域网
- 确认无鉴权需求（本机工具）；`--host 0.0.0.0` 需自行评估风险
- 停止服务时按「进程树」和「端口占用 PID」双重兜底，
  不会误杀其它 Python 进程（只认本启动器记录 + 指定端口）