# 设备状态监控 Web 端 —— 实现报告

> 日期：2026-11-18
> 前置：`docs/24_DEVICE_MONITOR_PROMPT.md`（开发提示词）
> 结果：**完成**。zjy13 新增 37 个测试（全量 521 通过），
> zjy14 前端 472 个测试仍全绿，端到端 47 + 25 项通过

---

## 一、交付内容

| 层 | 文件 | 改动 |
|---|---|---|
| zjy13 | `server/ws/server.py` | `ClientSession` 加监控字段、`snapshot()`、`process_request` 只读端点 |
| zjy13 | `tests/test_ws_snapshot.py` | **新增 37 个测试** |
| zjy13 | `scripts/verify_device_monitor.py` | 端点验证（25 项） |
| zjy13 | `scripts/verify_device_monitor_e2e.py` | 端到端验收（47 项） |
| zjy13 | `scripts/serve_monitor_demo.py` | 演示服务（替身 LLM/TTS，塞 3 块模拟板子） |
| zjy14 | `web/src/api/types.ts` | 设备监控类型（12 个接口） |
| zjy14 | `web/src/api/client.ts` | `deviceApi` |
| zjy14 | `web/src/hooks/useDeviceMonitor.ts` | 轮询 hook（含页面可见性处理） |
| zjy14 | `web/src/pages/DevicesPage.tsx` | **重写**（从静态占位页变成实时监控） |
| zjy14 | `web/src/components/ui.tsx` | `Card.title` 支持 ReactNode、`Metric` 加 `hint` |
| zjy14 | `web/src/vite-env.d.ts` | 新增（`import.meta.env` 类型） |

---

## 二、关键实现

### 2.1 zjy13 侧：快照是唯一数据出口

`snapshot()` 的四条硬约束（写在 docstring 里）：

```python
1. 只读：不修改任何状态，不创建任务，不发送消息
2. 不阻塞：纯内存读取，无 IO、无 await
3. 不抛异常：单个 client 出错不能影响其它 client
4. 字段缺失给默认值，不用 None 冒充 0
```

**第 3 条的实现**：逐个 client 包 try/except，出错的给占位对象：

```python
for client in list(self._clients.values()):
    try:
        clients.append(self._client_snapshot(client, now))
    except Exception as exc:
        clients.append({
            "session_id": getattr(client, "session_id", "?"),
            "error": f"快照生成失败: {exc}",
        })
```

有测试 `test_snapshot_broken_client_isolated` 验证：
一个 client 的 `state` 属性故意抛异常，另一个仍正常返回。

**时间换算**：`last_heartbeat` 和 `connected_at` 用 `time.perf_counter()` 基准，
前端拿到绝对时间戳无法比较，所以快照里直接给「距今多少秒」。

### 2.2 HTTP 端点：复用 WS 端口

用 `websockets.serve` 的 `process_request` 钩子，不另开端口：

```python
async with websockets.serve(
    self.handler, host, port,
    max_size=MAX_JSON_BYTES,
    process_request=self.process_request,   # ← 新增
):
```

**三个路径**：

| 路径 | 行为 |
|---|---|
| `GET /api/devices` | 完整快照 JSON |
| `GET /api/devices/health` | 极简探活 |
| `GET /api/devices/stream` | **返回 501**（见下） |
| 其它 | 返回 404 + 可用路径列表 |
| 非 `/api/devices` 开头 | 返回 `None` → 交给 WS 握手 |

**SSE 明确不支持**：`process_request` 只能返回一次性完整响应，无法流式推送。
所以 `/api/devices/stream` 返回 501 并说明替代方案：

```json
{
  "error": "sse_not_supported",
  "message": "未实现 SSE。请用轮询 GET /api/devices（建议 1 秒间隔）。",
  "poll_interval_ms": 1000
}
```

**为什么不假装支持**：返回一个假的 SSE 头会让前端以为能收到流，
实际只收到一次就断，比明确报错更难排查。

### 2.3 前端：轮询 hook 的四个细节

```typescript
1. 页面不可见时暂停（document.visibilityState）
2. 切回前台立即刷新一次（不等下一个间隔）
3. 请求失败保留上一次数据（UI 标记「可能过期」）
4. 组件卸载时中止进行中的请求（AbortController）
```

**为什么不用 `setInterval` 直接调 fetch**：
会堆积未完成的请求（上一次没回来又发下一次）。
hook 里每次 `fetchOnce` 先 `abort()` 上一次。

### 2.4 页面：8 种状态 + 5 类异常态

状态徽章配色（与 `states.py` 的 8 个枚举一一对应）：

| 状态 | 中文 | 颜色 |
|---|---|---|
| `idle` | 空闲 | 灰 |
| `recording` | 录音中 | 蓝 |
| `asr_processing` | 识别中 | 青 |
| `llm_processing` | 生成中 | 紫 |
| `tts_processing` | 合成中 | 橙 |
| `filler` | 填充音 | 黄 |
| `playing` | 播放中 | 绿 |
| `error` | 错误 | 红 |

异常态判定（全部基于源码常量，不是拍脑袋）：

| 异常 | 判定 | 展示 |
|---|---|---|
| 心跳超时 | `heartbeat_ok === false`（>30s） | 红色徽章 |
| 状态卡住 | `state_elapsed_ms > 60000`（TURN_TIMEOUT_S） | 黄色徽章 |
| 连接数满 | `active >= max_connections` | 顶部条「已满」 |
| 阶段偏慢 | ASR>1000 / LLM首字>1500 / TTS首块>3000ms | 指标下灰字「偏慢」 |
| 监控不可达 | 请求失败 | 顶部红点 + 排查指引 |

**流式模式的特殊处理**：

```typescript
// expect_frames=0 是合法的（总长未知），不能当异常
const pct = audio.progress !== null ? Math.round(audio.progress * 100) : null;
```

进度条在流式下显示半透明满格 + 「流式」徽章，而不是 0%。

### 2.5 空状态给 5 条排查指引

不是只写「暂无设备」，而是基于真实原因：

```
1. 板子是否连上 WiFi
2. 板子里的服务地址是否为 ws://<主机IP>:8765
3. 是否超过最大连接数（当前上限 4）
4. [仅当启用] 服务端已启用 access_token，板子是否带了正确令牌
5. 查看 zjy13 日志：grep "连接" <logfile>
```

第 4 条是条件渲染的 —— 只有 `access_token_required` 为 true 才显示。

---

## 三、踩到的三个坑

### 3.1 urllib 在同一事件循环里请求会死锁

第一版验证脚本用 `urllib.request.urlopen` 请求自己刚启动的 `serve()`：

```
[FAIL] 快照请求失败: TimeoutError: timed out
```

**原因**：`urllib` 是同步阻塞的。它运行在 `serve()` 所在的事件循环里，
请求发出后事件循环被阻塞，`serve()` 无法处理这个请求，于是互相等待。

**修复**：用 `asyncio.open_connection` 手写 HTTP 请求。

```python
reader, writer = await asyncio.open_connection("127.0.0.1", port)
writer.write(f"GET {path} HTTP/1.1\r\nHost: ...\r\n\r\n".encode())
raw = await asyncio.wait_for(reader.read(-1), timeout=5)
```

**这个坑很隐蔽**：单元测试全过（直接调 `process_request`），
只有真实 HTTP 请求才暴露。

### 3.2 `process_request` 拿不到 HTTP 方法

第一版写了 405 处理：

```python
method = request_headers.get("Method") or request_headers.get("method", "")
if method and method.upper() != "GET":
    return (405, ...)
```

**实测发现**：websockets 12 的 `Headers` 对象**只含 HTTP 头，不含请求方法**。
所以 `method` 恒为空，405 分支永远不触发。

**这比没写更糟** —— 代码看起来在防 POST，实际没防。

**修复**：删掉 405 逻辑，在 docstring 里明确说明这个限制，
并把测试改成断言「方法不可知，按路径响应」。

端点本身不提供任何写操作，POST 到只读端点也无副作用，所以可接受。
但**不能声称"只支持 GET"**（那是没验证过的说法）。

### 3.3 协议要求先 device_hello

验证 WebSocket 握手时直接发 `heartbeat`，收到的是 `error` 而非 `heartbeat_ack`。

**原因**：协议要求先 `device_hello` 完成握手，否则其它消息会被拒（`not_hello`）。

**修复**：验证脚本先发 `device_hello` 收 `server_hello`，再发心跳。

---

## 四、验收结果

### 4.1 zjy13 单元测试（37 个新增）

```
tests/test_ws_snapshot.py  37 passed
```

| 类别 | 数量 | 覆盖 |
|---|---|---|
| 空 clients | 3 | 结构、字段、角色信息 |
| 单 client | 3 | 字段完整性、timings、audio 形状 |
| 多 client | 2 | 互不干扰、状态隔离 |
| 心跳 | 3 | 新鲜、超时、已连接时长 |
| 音频进度 | 3 | 正常、流式（progress=null）、超界截断 |
| 状态透传 | 4 | 8 种枚举、filler、busy、error |
| 异常隔离 | 1 | 一个 client 炸不影响其它 |
| 计数器 | 4 | 初始值、连接、轮次、错误 |
| 只读性 | 2 | 不改状态、返回副本 |
| 辅助方法 | 2 | 错误环形缓冲、new_turn 重置 |
| HTTP 端点 | 11 | 快照、health、404、方法不可知、查询串、尾斜杠、WS 透传、SSE 501 |

### 4.2 zjy13 端点验证（25 项）

```
scripts/verify_device_monitor.py  25 passed
```

覆盖真实 HTTP 请求（不是直接调函数）、CORS、WebSocket 握手不被破坏、
连接数超限计数。

### 4.3 端到端验收（47 项）

```
scripts/verify_device_monitor_e2e.py  47 passed
```

3 块板子分别处于「播放中 / 空闲+心跳超时 / 录音中(流式)」，验证：

- 服务信息（角色、连接上限、采样率、协议版本）
- 状态透传（8 种枚举）
- 心跳判定（30s 阈值）
- 音频进度（含流式 `progress=null` 的特例）
- 阶段耗时（ASR/LLM/TTS/播放）
- 文本与错误
- 计数器（活动/累计连接/轮次/错误）
- **只读性**（多次快照状态不变）
- **前端契约字段完整性**（三组必填字段逐一核对）
- CORS 与禁缓存
- 设备断开后从快照消失

### 4.4 全量回归

```
zjy13: 521 passed, 1 skipped
zjy14: 472 passed
```

### 4.5 真实联调

启动 `scripts/serve_monitor_demo.py`（替身 LLM/TTS），
前端 dev server 跨端口读取：

```
active=3  clients=3  CORS=*
  esp32s3-01  state=playing    hb=41.6s  frames=98/134
  esp32s3-02  state=idle       hb=86.6s  frames=0/0
  esp32s3-03  state=recording  hb=41.6s  frames=17/0
```

**注意 `esp32s3-02` 的 `hb=86.6s`** —— 超过 30s 阈值，
前端会显示「心跳超时」红色徽章。这是刻意造的异常态，用于验证判定逻辑。

---

## 五、使用方式

### 5.1 真实环境（有板子）

```bash
# 1. 启动 zjy13（真实 ASR + LLM + TTS）
wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/start_all.sh

# 2. 启动 zjy14 前端
cd D:\xiangmudaima\zjy14\web
npm.cmd run dev

# 3. 浏览器打开 http://localhost:5173/devices
```

### 5.2 无板子时看效果

```bash
# 启动演示服务（塞 3 块模拟板子，覆盖 3 种状态）
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy13 && \
  source ~/cosyvoice-venv/bin/activate && \
  PYTHONPATH=. python scripts/serve_monitor_demo.py"
```

然后打开前端设备管理页，能看到 3 张卡片。

### 5.3 换监控地址

前端默认读 `http://127.0.0.1:8765`。若 zjy13 在别的机器/端口：

```bash
# web/.env.local
VITE_ZJY13_BASE=http://192.168.137.1:8765
```

---

## 六、已知限制

| 项 | 说明 |
|---|---|
| 无 SSE | `process_request` 无法流式，轮询 1 秒够用；明确返回 501 而非假装支持 |
| 无法区分 HTTP 方法 | websockets 12 的钩子拿不到方法；只读端点无副作用，可接受 |
| 看不到被拒连接详情 | 只统计次数（`rejected_connections`），不知道来源 IP |
| 重启后计数清零 | 计数器在内存，未持久化（设计如此：这是运行时监控，不是历史统计） |
| 事件流未做 | 提示词里列的「协议消息流」需要 zjy13 额外维护环形缓冲，本次未做 |
| 无设备控制 | 刻意不做（只读监控） |

---

## 七、与提示词的差异

实现时对提示词做了三处调整，都是实测后发现的：

| 提示词原方案 | 实际实现 | 原因 |
|---|---|---|
| SSE 推送 | 轮询 + SSE 返回 501 | `process_request` 不支持流式 |
| 只允许 GET（405） | 不做方法判断 | 钩子拿不到 HTTP 方法 |
| 事件流（协议消息） | 未做 | 需要额外维护缓冲，优先级低于核心状态 |

前两项已在代码注释和本文档里说明，不是遗漏。

---

## 八、复现命令

```bash
# zjy13 快照测试
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy13 && source ~/cosyvoice-venv/bin/activate && \
  PYTHONPATH=. python -m pytest tests/test_ws_snapshot.py -q"

# zjy13 端点验证（真实 HTTP）
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy13 && source ~/cosyvoice-venv/bin/activate && \
  PYTHONPATH=. python scripts/verify_device_monitor.py"

# 端到端验收
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy13 && source ~/cosyvoice-venv/bin/activate && \
  PYTHONPATH=. python scripts/verify_device_monitor_e2e.py"

# zjy13 全量
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy13 && source ~/cosyvoice-venv/bin/activate && \
  PYTHONPATH=. python -m pytest tests/ -q"

# zjy14 全量
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/run_tests.sh

# 前端构建
cd D:\xiangmudaima\zjy14\web && npm.cmd run build
```

---

## 九、经验教训

1. **同步 HTTP 客户端不能在同一事件循环里请求自己。** `urllib` 会阻塞循环，
   表现为「单元测试全过但真实请求超时」。必须用 `asyncio.open_connection`。

2. **没验证过的防护等于没有防护。** 405 逻辑看起来在防 POST，
   实际 `process_request` 拿不到方法，分支永远不触发。
   这比不写更糟 —— 它给人一种虚假的安全感。

3. **不支持的就要明确报错。** SSE 返回 501 + 替代方案，
   比返回一个假的 `text/event-stream` 头好得多。

4. **异常隔离必须测。** 「一个 client 出错不影响其它」这条约束，
   不写测试根本发现不了（正常情况下不会触发）。

5. **测试数据要覆盖边界。** 流式 `expect_frames=0` 时
   `progress` 必须是 `null` 而不是 `0` —— 0% 会让人以为卡住了，
   `null` 才正确表达「总长未知」。
