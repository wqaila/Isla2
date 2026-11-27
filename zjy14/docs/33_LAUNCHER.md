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


**注意：光有 BOM 不够。** BOM 只解决「编码被误读」，脚本仍可能真有语法错误
（漏括号、参数拼错）。所以测试里还加了一道**用 PowerShell 自己的解析器做静态
校验**（`System.Management.Automation.Language.Parser.ParseFile`）——
只解析、不执行，因此不会拉起任何进程：

```powershell
$e=$null;$t=$null
[void][System.Management.Automation.Language.Parser]::ParseFile('scripts\start_web.ps1',[ref]$t,[ref]$e)
if($e){$e|%{$_.Message}}
```

这两个坑合起来说明一件事：**PowerShell 脚本的失效是"静默且彻底"的** ——
编码错或语法错都会让整个脚本一行都不执行，而调用方只看到一个莫名其妙的报错。

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


### 6.5 停止 WSL 服务：绝不能用「按端口杀」（实测事故，已修）

用户反馈「点停止 TTS，弹出『已终止占用端口 50001 的进程 [11180]』，但 TTS 其实没停」。
查证后确认是**真的没停**，而且界面还给了一个假成功。

**根因**：WSL2 里的服务在 Windows 侧看到的监听者并不是 uvicorn，而是
WSL 的端口转发进程 `wslrelay`：

```
TCP  127.0.0.1:50001  LISTENING  11180   ← wslrelay.exe（不是 TTS）
WSL 内: python -m custom_voice.server (PID 339, RSS 5.7GB)  ← 真正在跑的服务
```

旧的停止逻辑在「端口仍被占用」时走兜底：按端口找 PID 再 `taskkill /T /F`。
对 WSL 服务这是**三步全错**：

| 后果 | 说明 |
|---|---|
| 服务没停 | 杀的是转发进程，WSL 内 uvicorn 继续跑、继续占 5.7GB 显存 |
| 假成功 | 界面显示「已终止占用端口 50001 的进程」，用户以为停了 |
| 破坏环境 | 打断 WSL 的 localhost 转发（其它端口也受影响），且 WSL 会立刻重新拉起它，端口照样在监听 |

**修法**：

1. 新增 `NEVER_KILL_PROCESS_NAMES`（`wslrelay`/`wslhost`/`vmmem`/`lsass`… 16 项），
   按端口兜底时先查进程名，命中就跳过并说明原因；
2. **WSL 服务（`kind == "wsl"`）完全不走按端口杀**，只走 `pkill`；
3. `pkill` 失败时返回 `ok=False` + `action="stop_failed"` + `needs_manual`，
   **不再因为「端口恰好没了」而报成功**，并把可执行命令带在 `manual_command` 里。

顺带修了进程名探测：受限上下文里 `tasklist` 可能**完全看不到**目标进程
（全量列表里都没有那一行），于是「是不是系统进程」的判断失效。
改用 `ctypes` 的 `OpenProcess` + `QueryFullProcessImageNameW` 按 PID 直查，
tasklist 退为兜底。

**另一个相关 bug**：`start_web.ps1 -Stop` 原来用 `taskkill /PID <launcher> /T /F`，
`/T` 会把启动器的子进程（ASR/网关）一起杀掉，脚本却打印
「语音链路服务不会被停止」—— 相反的话。已改为先用 `/api/stop-all`
**有序停止**，再不带 `/T` 结束启动器。

> 教训：**「端口没了」不等于「服务停了」**。判断服务是否真的停止，
> 要看它的健康检查/进程，而不是端口的可达性 —— 中间可能隔着转发层。


### 6.6 当启动器无法访问 WSL 时怎么办（已给出解法）

有些环境下，**Python 及其整个进程树**调用 WSL 一律返回
`Wsl/E_ACCESSDENIED`，而同样的命令在用户的 PowerShell 里正常。
已穷举验证，全部被拒：

| 尝试过的路径 | 结果 |
|---|---|
| `wsl.exe`（stdio：管道 / 文件 / 新控制台 / 继承，四种都试） | 拒绝 |
| 经 `cmd.exe` 转一层 | 拒绝 |
| 经 `powershell.exe -Command` / `-File` | 拒绝 |
| `ctypes` 直调 `wslapi.dll` 的 `WslLaunch` | 拒绝（`0x80070005`） |
| `schtasks` 建一次性任务跳出进程树 | `schtasks` 自身「找不到路径」 |
| 读注册表 `HKCU\...\Lxss` 确认发行版名 | ✅ 可读（说明不是发行版名写错） |

结论：限制在**进程令牌**层面，Python 侧无法自救。但**PowerShell 侧完全正常**。

#### 解法：三个产物

| 脚本 | 作用 |
|---|---|
| `scripts/start_tts.ps1` | 纯 PowerShell 启动 TTS（不经过 Python） |
| `scripts/stop_tts.ps1` | 纯 PowerShell 停止 TTS |
| 页面顶部横幅 | `wsl_available=false` 时**提前**提示该用这两个脚本 |

页面不会再让你点一次失败一次：`/api/status` 返回 `wsl_available`，
为 false 时页面顶部直接显示提示和命令，TTS 卡片的按钮也带 `title` 说明。

#### `start_tts.ps1` 为什么要用「隐藏 powershell 持有 wsl」

TTS 需要一个**常驻的 wsl.exe 会话**，否则 WSL2 会在会话结束后关掉 VM，
把里面的 uvicorn 一起带走。实测三种写法都是秒退、日志 0 字节：

| 写法 | 结果 |
|---|---|
| `setsid nohup ... &`（脚本立刻返回） | 秒退（会话结束 → VM 关闭） |
| `Start-Process wsl.exe -WindowStyle Hidden` | 秒退 |
| `Start-Process wsl.exe`（不带 Hidden） | 秒退 |

可行做法：起一个**隐藏的 powershell**，在它里面**前台**跑 `wsl`：

```powershell
Start-Process powershell.exe -WindowStyle Hidden `
  -ArgumentList @("-NoProfile","-Command","wsl.exe -d Ubuntu-24.04 -e bash /mnt/d/.../_start_tts.sh") `
  -RedirectStandardOutput logs\tts_manual.log -RedirectStandardError logs\tts_manual.err.log
```

实测：**17 秒**冷启动完成，`engine=cosyvoice3 device=cuda load_s=9.08`。
持有者 PID 记在 `logs\tts_holder.pid`，`stop_tts.ps1` 会一并清理。

#### 两个编码踩坑

1. **不要用 bash heredoc 写 WSL 脚本**：多行字符串经 `wsl.exe` 参数传递会被
   截断，实测文件根本没创建。改为**从 Windows 侧直接写**到共享目录
   （WSL 从 `/mnt/d` 可见）。
2. **写出的 `.sh` 必须是 LF 换行**：bash 遇到 CRLF 会报
   `$'\r': command not found`。用 `[System.IO.File]::WriteAllText` +
   `-join "`n"` 显式控制。

> 设计取向：**能力不足时不要假装成功**。宁可如实报告 + 给一条确定可用的命令，
> 也不要在界面上造一个「已终止」的假象 —— 后者会让人以为服务停了，
> 而实际上显存还占着。

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

### 6.4 把脚本输出接管道时，调用方会等到服务退出

实测（2026-11-27）：

| 场景 | 结果 |
|---|---|
| 双击 `.bat` / 直接运行 `.ps1`（stdout 是控制台或文件） | 冷启动 **2.3 秒**返回 |
| `.\start_web.ps1 \| Tee-Object log.txt`（stdout 是管道） | 调用方等到**服务全部退出**才返回（实测 398 秒） |

原因：`Start-Process` 走 `CreateProcess(bInheritHandles=TRUE)`，子进程会继承
父进程的**可继承句柄**。即使把子进程 stdout 重定向到文件，它仍持有一份父进程
管道的写端，管道到不了 EOF。

这不是脚本缺陷，也无法用纯 PowerShell 绕开（彻底断开需要 WMI
`Win32_Process::Create` 或 P/Invoke，代价是丢失服务日志）。
**日常使用不受影响** —— 双击 .bat 或在终端里直接运行都没有管道。

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
- `test_web_integration.py`（21 项）：**整合页能力对齐**（进度面板、
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
| `scripts/start_tts.ps1` | **纯 PowerShell 启动 TTS**（绕过 Python 的 WSL 限制） |
| `scripts/stop_tts.ps1` | 单独停止 WSL 内的 TTS（并清理持有者进程） |
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
| `tests/test_web_integration.py` | 21 项：整合页能力对齐、Vite 绑定/代理、ps1 BOM 与**语法解析校验** |

---

## 十、安全边界

控制台能**启动本机任意进程**，所以：

- 默认只监听 `127.0.0.1`，不暴露到局域网
- 确认无鉴权需求（本机工具）；`--host 0.0.0.0` 需自行评估风险
- 停止服务时按「进程树」和「端口占用 PID」双重兜底，
  不会误杀其它 Python 进程（只认本启动器记录 + 指定端口）