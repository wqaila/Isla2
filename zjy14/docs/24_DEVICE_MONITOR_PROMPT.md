# 设备状态监控 Web 端 —— 开发提示词

> 目标：为 ESP32-S3 语音终端做一个**实时状态监控 Web 页面**，
> 在板子连接后能看到连接状态、对话流程、音频下发、错误等一切运行时信息。
> 本文档基于对 `zjy13` 与 `zjy14` 真实代码的核查（2026-11-18），
> 所有字段名、常量、路径都可在源码中验证。

---

## 零、先读这一段（架构约束，决定方案）

### 0.1 设备状态在哪里

```
ESP32-S3 固件
    │  WebSocket (ws://127.0.0.1:8765)
    ▼
zjy13/server/ws/server.py          ← 设备连接、会话状态全在这里
    │  HTTP (:50000 或 :50001)
    ▼
zjy14 TTS 引擎                      ← 只有合成能力，不知道设备存在
```

**关键事实**：设备状态**只存在于 zjy13 进程的内存里**。

证据（`zjy13/server/ws/server.py:199`）：

```python
self._clients: dict[str, ClientSession] = {}
```

`ClientSession`（同文件 `:122`）持有全部运行时状态：

| 字段 | 含义 |
|---|---|
| `session_id` | 会话 ID |
| `device_id` | 设备自报的标识（`device_hello` 里带） |
| `state` | 状态机实例（`DialogueStateMachine`） |
| `hello_done` | 是否已完成握手 |
| `turn_id` / `turn_seq` | 当前轮次 ID 与帧序号 |
| `last_heartbeat` | 最后心跳时间（`time.perf_counter()`） |
| `audio` | `TurnAudio \| None`，本轮音频累积 |
| `expect_audio_bytes` / `expect_audio_frames` | 本轮应下发的字节/帧数（用于 `playback_done` 校验） |
| `filler_session_open` | 填充音是否已开播放会话 |
| `dialogue` | `ConversationSession`（对话历史） |

**结论：zjy14 拿不到这些数据。** 按决策 D7 的职责边界，
zjy14 不碰 WebSocket 层。

### 0.2 当前 serve() 只有 WebSocket，没有 HTTP

`zjy13/server/ws/server.py:1276`：

```python
async def serve(self) -> None:
    async with websockets.serve(
        self.handler, self.config.host, self.config.port,
        max_size=MAX_JSON_BYTES, ping_interval=20, ping_timeout=20,
    ):
        await asyncio.Future()
```

**没有 HTTP 端口、没有 REST 接口、没有静态文件服务。**

### 0.3 因此只有三条可行路径

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A** | 给 zjy13 加 HTTP 端点（同一端口用 `websockets` 的 `process_request`，或另起 aiohttp） | 数据源正确，架构合理 | 要改 zjy13 |
| **B** | 独立观察者进程，直连 zjy13 的 WS 当"第二个客户端" | 不改 zjy13 | 会占用 `max_connections`；无法看到别人的会话 |
| **C** | 在 zjy13 内嵌一个只读 Web 页面 | 数据天然可达，零跨进程 | 页面代码进 zjy13 仓库 |

**推荐 A**。理由：
- 设备状态本来就该由持有它的服务暴露（单一数据源）
- zjy14 前端已经有一套完整的技术栈（React + TS + Vite），可以复用
- 加一个只读端点对 zjy13 是纯增量，不动现有逻辑

**本提示词按方案 A 写**。若你选 B 或 C，对应章节会标注差异。

---

## 一、交付目标

### 1.1 一句话目标

打开浏览器，能看到**每块板子**当前的：连接状态、对话阶段、本轮进度、
音频下发量、心跳、错误历史，并且**不用刷新**（WebSocket 或 SSE 推送）。

### 1.2 必须回答的 8 个问题

这个页面存在的意义就是让下面这些问题一眼可见：

| # | 问题 | 数据来源 |
|:-:|---|---|
| 1 | 板子连上了吗？ | `_clients` 是否非空 |
| 2 | 连了几块？还有空位吗？ | `len(_clients)` / `max_connections` |
| 3 | 每块板子现在在干什么？ | `session.state.state`（`State` 枚举） |
| 4 | 这一轮走到哪一步了？ | `state.state` + `state.turn_id` + `state.elapsed_ms` |
| 5 | 音频发了多少？板子播完了吗？ | `expect_audio_bytes` / `expect_audio_frames` |
| 6 | 心跳还活着吗？多久没心跳了？ | `last_heartbeat` |
| 7 | 最近出过什么错？ | 错误消息历史 |
| 8 | 端到端延迟多少？ | 各阶段时间戳差 |

### 1.3 非目标（明确不做）

- ❌ 不做设备控制（重启、OTA、改配置）—— 只读监控
- ❌ 不做历史数据持久化（内存环形缓冲即可，重启即清）
- ❌ 不做多用户权限（本机工具，`127.0.0.1` 即可）
- ❌ 不做移动端适配（桌面调试工具）

---

## 二、数据源：需要 zjy13 暴露什么

### 2.1 新增 HTTP 端点（方案 A）

在 `zjy13` 里加一个只读端点。**建议复用现有 WS 端口**，
用 `websockets.serve` 的 `process_request` 钩子处理 HTTP 请求，
避免多开端口：

```python
# 伪代码，示意位置：server/ws/server.py 的 serve()
async def process_request(path, request_headers):
    if path == "/api/devices":
        return http.HTTPStatus.OK, [], json.dumps(snapshot()).encode()
    if path == "/api/devices/stream":   # SSE
        return None   # 交给 websocket 握手（不是 HTTP 请求时返回 None）
    return None
```

> 若觉得 `process_request` 太 hack，也可以另起 aiohttp（多一个端口，
> 例如 `127.0.0.1:8766`）。两者都可，但**必须只读**。

### 2.2 快照数据结构（必须包含的字段）

```jsonc
{
  "server": {
    "host": "127.0.0.1",
    "port": 8765,
    "character_id": "elysia",
    "character_name": "爱莉希雅",
    "uptime_s": 1234.5,
    "max_connections": 4,
    "access_token_required": false,
    "audio_in_sample_rate": 16000,
    "audio_in_channels": 4,
    "audio_out_sample_rate": 24000
  },
  "clients": [
    {
      "session_id": "a1b2c3",
      "device_id": "esp32s3-01",
      "peer": "192.168.137.42:51234",
      "connected_at": "2026-11-18T09:00:00+08:00",
      "connected_s": 320.5,
      "hello_done": true,

      "state": "playing",              // 见 2.3 的枚举
      "state_since_s": 1.2,            // 进入当前状态多久
      "turn_id": "a1b2c3-t48210",
      "turn_seq": 12,                  // 已发出的音频帧序号

      "last_heartbeat_s": 3.4,         // 距上次心跳多少秒
      "heartbeat_ok": true,            // < HEARTBEAT_TIMEOUT_S(30s)

      "audio": {
        "expect_bytes": 547200,
        "expect_frames": 134,
        "sent_bytes": 400000,
        "sent_frames": 98,
        "progress": 0.73               // sent_frames / expect_frames
      },

      "filler_session_open": false,
      "dialogue_turns": 5,             // 对话历史轮数

      "last_error": null,              // 或 { code, message, at }
      "errors_recent": [               // 最近 N 条，环形缓冲
        { "code": "tts_failed", "message": "...", "at": "..." }
      ],

      "timings": {                     // 本轮各阶段耗时（毫秒）
        "asr_ms": 120,
        "llm_first_ms": 646,
        "tts_first_ms": 1685,
        "playback_ms": null            // 还没收到 playback_done
      }
    }
  ],
  "counters": {
    "total_turns": 42,
    "total_errors": 3,
    "rejected_connections": 1
  }
}
```

### 2.3 状态枚举（必须与 zjy13 一致）

来自 `zjy13/server/ws/states.py:20`：

| 值 | 含义 | UI 建议颜色 |
|---|---|---|
| `idle` | 空闲，等板子按按钮 | 灰 |
| `recording` | 板子在录音上传 | 蓝 |
| `asr_processing` | ASR 识别中 | 青 |
| `llm_processing` | LLM 生成中 | 紫 |
| `tts_processing` | TTS 合成中 | 橙 |
| `filler` | 播放填充音（「嗯…」） | 黄 |
| `playing` | 板子播放回复 | 绿 |
| `error` | 出错 | 红 |

> **必须用这 8 个值**，不要自造。源码里还有 `TURN_CANCELLED` 之类的
> 消息类型，但状态机只有这 8 个状态。

### 2.4 关键常量（写死在 UI 里，用于判断异常）

来自 `zjy13/server/ws/server.py`：

| 常量 | 值 | 用途 |
|---|---|---|
| `HEARTBEAT_TIMEOUT_S` | 30.0 | 超过则心跳异常 |
| `TURN_TIMEOUT_S` | 60.0 | 单轮总超时 |
| `PLAYBACK_MIN_TIMEOUT_S` | 20.0 | 播放看门狗下限 |
| `PLAYBACK_MAX_GUESS_S` | 120.0 | 播放看门狗上限 |
| `MAX_JSON_BYTES` | 64 KB | JSON 消息上限 |
| `MAX_AUDIO_CHUNK_BYTES` | 48 KB | 单个音频帧上限 |
| `PROTOCOL_VERSION` | 1 | 协议版本 |
| `BINARY_MAGIC` | 0xA5 | 二进制帧魔数 |
| `BINARY_HEADER_SIZE` | 12 | 帧头字节数 |

**UI 必须用这些真实值做阈值判断**，不能凭感觉写"3 秒没心跳就报警"。

### 2.5 推送方式：SSE 还是轮询

| 方式 | 实现 | 延迟 | 复杂度 |
|---|---|---|---|
| **SSE**（推荐） | `GET /api/devices/stream`，`text/event-stream` | ~1s | 中 |
| 轮询 | `GET /api/devices`，前端 `setInterval(1000)` | 1s | 低 |
| WebSocket | 另开一个 WS 端点 | ~0.5s | 高（与设备 WS 混在一起容易乱） |

**推荐 SSE**：
- 单向推送，语义正好
- 浏览器原生 `EventSource`，不用额外库
- 断线自动重连（浏览器内置）
- 不会与设备 WS 的 `max_connections` 冲突

**若想先做简单的**：轮询 1 秒一次完全够用，且实现成本最低。
**建议先做轮询版跑通，再加 SSE**。

### 2.6 错误码（用于错误历史展示）

来自 `zjy13/server/protocol/messages.py`：

```
protocol_version_mismatch  bad_message        not_hello
unknown_type              no_such_turn        turn_busy
audio_too_large           audio_buffer_overflow  text_too_long
asr_failed                asr_empty           llm_failed
tts_failed                tts_unavailable     cancelled
internal
```

UI 应按这些 code 分类展示，而不是只显示原始字符串。

---

## 三、页面设计

### 3.1 整体布局

```
┌─────────────────────────────────────────────────────────────┐
│ 顶部状态条                                                   │
│  ● 服务运行中  ws://127.0.0.1:8765  角色:爱莉希雅            │
│  连接 1/4  运行 5m32s  累计 42 轮  错误 3 次                 │
├─────────────────────────────────────────────────────────────┤
│ 设备卡片（每块板子一张，无设备时显示空状态）                   │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ esp32s3-01        ● playing          peer 192.168.137.42│ │
│  │ 会话 a1b2c3       连接 5m20s         心跳 3.4s 前 ✅    │ │
│  │───────────────────────────────────────────────────────│  │
│  │ 本轮进度  ████████████████░░░░  98/134 帧 (73%)        │  │
│  │ 状态流: idle → recording → asr → llm → tts → playing  │  │
│  │───────────────────────────────────────────────────────│  │
│  │ ASR 120ms   LLM 646ms   TTS 1685ms   播放 —           │  │
│  │───────────────────────────────────────────────────────│  │
│  │ 最近错误: （无）                                        │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
│  （空状态）                                                   │
│  ┌───────────────────────────────────────────────────────┐  │
│  │          暂无设备连接                                   │  │
│  │  板子需要连到 ws://127.0.0.1:8765                       │  │
│  │  排查：① 板子是否连上 WiFi  ② 服务地址是否正确           │  │
│  │        ③ 是否超过最大连接数  ④ 是否需要 access_token     │  │
│  └───────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│ 事件流（最近的协议消息，倒序）                                │
│  09:01:23  a1b2c3  playback_start  frames=0 streaming=true   │
│  09:01:22  a1b2c3  tts_start                                  │
│  09:01:20  a1b2c3  asr_final  "今天天气怎么样"                │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 每张设备卡片必须显示的信息

**第一行（身份）**
- `device_id`（空则显示 session_id 前 8 位）
- `session_id`
- `peer`（IP:端口）
- 状态徽章（8 种颜色）

**第二行（健康）**
- 已连接时长（`connected_s` 格式化）
- 心跳：`last_heartbeat_s` 秒前
  - `< 10s` → 绿色 ✅
  - `10–30s` → 黄色 ⚠️
  - `> 30s` → 红色 ❌（超过 `HEARTBEAT_TIMEOUT_S`）
- `hello_done` 是否完成握手

**第三行（本轮进度）**
- 进度条：`sent_frames / expect_frames`
- 若 `expect_frames == 0`（流式模式，总长未知）→ 显示「流式下发中」+ 已发帧数
- `turn_id` 与 `turn_seq`

**第四行（阶段耗时）**
- ASR / LLM 首字 / TTS 首块 / 播放总时长
- 未完成的显示 `—`
- 超过阈值的标红（如 TTS 首块 > 3000ms）

**第五行（错误）**
- 最近 3 条错误（code + message + 时间）
- 无错误显示「无」

### 3.3 状态流转可视化（可选但推荐）

用一行小圆点展示 8 个状态，当前状态高亮：

```
idle ─ recording ─ asr ─ llm ─ tts ─ filler ─ playing
              ●
```

**注意**：状态机不允许任意跳转（`IllegalTransitionError`），
所以高亮应该是「当前状态」，不是「走过的路径」。

### 3.4 空状态必须给排查指引

无设备时不能只显示「暂无设备」。要列出排查步骤（基于真实原因）：

1. 板子是否连上 WiFi
2. 板子里的服务地址是否为 `ws://<主机IP>:8765`
3. 是否超过 `max_connections`（默认 4）
4. 若配了 `access_token`，板子是否带了正确令牌
5. 看 zjy13 日志：`grep "连接" <logfile>`

### 3.5 异常态必须显眼

| 异常 | 判定 | 展示 |
|---|---|---|
| 心跳超时 | `last_heartbeat_s > 30` | 卡片红边框 + 「心跳超时」 |
| 状态卡住 | 同一状态停留 > 60s（`TURN_TIMEOUT_S`） | 黄色警告「可能卡住」 |
| 播放看门狗触发 | 日志里有对应记录 | 红色标记 |
| 音频帧数不符 | `sent_frames != expect_frames` 且状态已回 idle | 警告 |
| 连接数满 | `len(clients) == max_connections` | 顶部条提示「已满，新连接会被拒」 |

---

## 四、技术实现要求

### 4.1 技术栈

**若复用 zjy14 前端**（推荐）：

| 项 | 选择 | 理由 |
|---|---|---|
| 框架 | React 18 + TypeScript 5 | 与 `zjy14/web` 一致，可直接复用组件 |
| 构建 | Vite 5 | 同上 |
| 样式 | 原生 CSS | `zjy14/web/src/styles.css` 已有暗色主题 |
| 路由 | 不加新路由 | 作为 `/devices` 页重写现有占位页 |
| 状态 | 现有 `useAsync` hook | 已有，够用 |

**若在 zjy13 内嵌**（方案 C）：
用单文件 HTML + 原生 JS，不引入构建链，避免给 zjy13 增加前端依赖。

### 4.2 数据获取层要求

```typescript
// 必须做的事
1. 轮询间隔可配置，默认 1000ms
2. 页面不可见时暂停轮询（document.visibilityState）
3. 请求失败时保留上一次数据 + 显示"数据可能过期"
4. 不用 any，所有字段有类型定义
5. 后端返回未知字段时不崩溃（可选字段用 ?）
```

### 4.3 必须处理的边界

| 场景 | 期望行为 |
|---|---|
| 后端未启动 | 显示「无法连接监控服务」+ 重试按钮，不白屏 |
| 返回 `clients: []` | 显示空状态 + 排查指引 |
| 某字段缺失 | 显示 `—`，不报错 |
| `state` 是未知值 | 显示原始字符串 + 灰色徽章，不崩溃 |
| 心跳字段为 0 | 显示「无心跳记录」 |
| 连接数达到上限 | 顶部条明确提示 |

### 4.4 性能要求

- 轮询 1 秒，单次响应 < 50KB
- 设备数 ≤ 4（`max_connections` 默认 4），无需虚拟滚动
- 事件流最多保留最近 200 条（前端环形缓冲）

---

## 五、zjy13 侧改动清单（方案 A）

### 5.1 必须新增

| 项 | 位置 | 说明 |
|---|---|---|
| 快照构造函数 | `server/ws/server.py` | 从 `_clients` 提取为 dict |
| HTTP 端点 | `serve()` 里加 `process_request` | 或另起 aiohttp |
| 事件环形缓冲 | `ClientSession` 加字段 | 记录最近 N 条协议消息 |
| 阶段计时 | 各阶段起止处打时间戳 | ASR/LLM/TTS 耗时 |

### 5.2 不得改动

- ❌ 不改协议（`protocol/messages.py`）
- ❌ 不改状态机（`ws/states.py`）
- ❌ 不改固件
- ❌ 不改现有 WS 处理流程

### 5.3 快照构造要求

```python
def snapshot(self) -> dict:
    """只读快照。必须满足：
       1. 不阻塞事件循环（纯内存读取，无 IO）
       2. 不修改任何状态（只读）
       3. 字段缺失时给默认值，不抛异常
       4. 单个 client 出错不影响其它 client
    """
```

**关键**：`state` 字段要能拿到当前状态字符串。`DialogueStateMachine`
（`states.py:78`）的公开属性是：

| 属性 | 类型 | 说明 |
|---|---|---|
| `.state` | `State` | 当前状态枚举 |
| `.turn_id` | `str \| None` | 当前轮次 ID |
| `.is_busy` | `bool` | 是否处于忙状态（`BUSY_STATES`） |
| `.elapsed_ms` | `float` | 进入当前状态已多久（毫秒） |

注意是 `.state.state`（`ClientSession.state` 是状态机实例，
它的 `.state` 才是枚举），不是 `.state.current`。**别猜，去看源码。**

---

## 六、验收标准

### 6.1 功能验收（必须全过）

| # | 场景 | 判定 |
|:-:|---|---|
| 1 | 后端未启动 | 页面显示错误提示，不白屏 |
| 2 | 无设备连接 | 显示空状态 + 5 条排查指引 |
| 3 | 板子连上 | 1 秒内出现设备卡片 |
| 4 | 板子断开 | 1 秒内卡片消失 |
| 5 | 对话进行中 | 状态徽章实时变化（idle→recording→…→playing→idle） |
| 6 | 播放中 | 进度条随 `sent_frames` 增长 |
| 7 | 心跳超时 | 卡片变红 + 显示「心跳超时」 |
| 8 | 发生错误 | 错误码出现在卡片与事件流 |
| 9 | 多设备 | 2 块板子各一张卡片，互不干扰 |
| 10 | 连接数满 | 顶部条提示「已满」 |
| 11 | 页面切到后台 | 轮询暂停（可用 DevTools Network 验证） |
| 12 | 页面切回前台 | 立即刷新一次 |

### 6.2 数据准确性验收

**必须与实际日志交叉验证**：

1. 板子跑一轮对话
2. 页面显示的状态变化顺序，必须与 zjy13 日志里的状态流转一致
3. 页面显示的 `expect_frames`，必须等于日志里的「帧=N」
4. 页面显示的 ASR/LLM/TTS 耗时，与日志里的对应数字误差 < 5%

**这一条不能省**。监控页面最大的风险是"显示得好看但数字是错的"。

### 6.3 不伪造数据

- 无设备时不显示假的设备卡片
- 字段缺失显示 `—`，不显示 0 或猜测值
- 不编造"在线率""健康分"之类没有数据支撑的指标

---

## 七、常见坑（基于本项目实际踩过的）

| 坑 | 说明 |
|---|---|
| **别用 `time.time()` 算心跳差** | zjy13 用 `time.perf_counter()`（`server.py:144`），两者基准不同。快照里应直接给出「距上次心跳秒数」，不要让前端自己算 |
| **`expect_frames == 0` 是合法的** | 流式模式下总长未知，`playback_start` 里 `frames=0`。不能当异常 |
| **状态机有非法跳转** | 不要假设状态一定按顺序走（例如填充音会插到 `filler`）。只显示当前值 |
| **填充音会占用 seq** | `filler_session_open` 时正文接在填充音后面，`turn_seq` 不是从 0 开始 |
| **`max_connections` 满时连接被拒** | 但 `_clients` 里看不到被拒的尝试，需单独计数（`rejected_connections`） |
| **PowerShell 读 .ps1 中文要 BOM** | 若写启动脚本，必须用 `UTF8Encoding($true)`（本项目已踩两次） |
| **不要用 PowerShell 做源码替换** | `\f` 会被当转义符，`from` 变 `rrom`（本项目踩过，损坏 46 处） |
| **WSL 里 `wsl -e bash -lc "..."` 会吞引号** | 复杂命令写成 `.sh` 文件 |

---

## 八、交付物清单

### 8.1 必交付

| 项 | 路径（方案 A） |
|---|---|
| 前端页面 | `zjy14/web/src/pages/DevicesPage.tsx`（重写现有占位页） |
| API 类型 | `zjy14/web/src/api/types.ts` 追加 `DeviceSnapshot` 等 |
| API 客户端 | `zjy14/web/src/api/client.ts` 追加 `deviceApi` |
| 后端端点 | `zjy13/server/ws/server.py` 加 `process_request` |
| 快照函数 | `zjy13/server/ws/server.py` 加 `snapshot()` |
| 单元测试 | `zjy14/tests/` 或 `zjy13/tests/` 覆盖快照构造 |
| 验收脚本 | `scripts/verify_device_monitor.sh` |
| 文档 | 说明数据来源、字段含义、排查指引 |

### 8.2 测试要求

| 类型 | 最少数量 | 覆盖 |
|---|---|---|
| 快照构造单元测试 | 8 | 空 clients、单 client、多 client、字段缺失、异常 client |
| 前端类型检查 | 1 | `tsc --noEmit` 零错误 |
| 前端构建 | 1 | `npm run build` 成功 |
| 端到端验收 | 12 | 见 6.1 |

### 8.3 文档要求

必须写清楚：

1. 数据从哪来（哪个文件、哪个字段）
2. 每个字段的含义与单位
3. 8 种状态的含义
4. 阈值为什么是这个值（引用源码常量）
5. 无设备时的排查步骤
6. 已知限制（如：看不到被拒的连接、重启后计数清零）

---

## 九、实施顺序建议

| 步骤 | 内容 | 预计 |
|---|---|---|
| 1 | zjy13 加 `snapshot()` + 单元测试（**先不碰 HTTP**） | 1h |
| 2 | zjy13 加 HTTP 端点，用 `curl` 验证 JSON | 0.5h |
| 3 | 前端类型 + API 客户端 + 页面骨架（假数据渲染） | 1.5h |
| 4 | 接真实数据，轮询跑通 | 1h |
| 5 | 异常态样式（心跳超时、卡住、连接满） | 1h |
| 6 | 事件流（若 zjy13 有环形缓冲） | 1h |
| 7 | 验收脚本 + 数据准确性交叉验证 | 1h |
| 8 | 文档 | 0.5h |

**总计约 7.5 小时**（不含真机调试时间）。

> 第 1 步必须先做，且必须先有测试。快照函数是唯一的数据出口，
> 它错了后面全错。

---

## 十、给实现者的三句话

1. **数据只有 zjy13 有，别在 zjy14 里编。**
   查 `zjy13/server/ws/server.py:199` 的 `_clients`，
   以及 `:122` 的 `ClientSession` 字段定义。

2. **状态枚举、阈值常量必须从源码抄，不能凭感觉。**
   `states.py:20` 有 8 个状态，`server.py` 有 `HEARTBEAT_TIMEOUT_S=30` 等。

3. **验收必须与实际日志交叉验证。**
   页面显示的数字对不上日志，比没有页面更糟——
   它会让人做出错误判断。
