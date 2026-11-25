# 2026-11-25 zjy14 全面体检：以 zjy13 为基准的契约对齐与隐患治理

> **起因**：用户要求「查看 zjy14 文件夹，以 zjy13 为基准，结合 zjy14 的新功能，
> 完善 zjy14，解决其中的隐患」。
>
> **基准**：`zjy13`（2026-11-25 刚完成 12 个缺陷的修复，含真机串口验证）。
> zjy14 是 zjy13 的**引擎升级版**（决策 D2），复用固件与 WebSocket 协议，
> 只替换 TTS 引擎 —— 所以 zjy13 的修复结论**必须**传导到 zjy14。
>
> **结果**：定位并修复 **7 个隐患 + 4 处契约漂移**，
> 测试从 **483 → 517**（新增 34 项回归，全绿）。

---

## 零、最关键的一条

> **P0：`/synthesize_stream` 在 `async def` 里同步等待信号量，
> 会把整个 FastAPI 事件循环冻住最长 60 秒。**
>
> 这不是「慢」，是**整个服务停摆** —— 期间 `/health`、监控页、
> 系统状态、以及**正在传输的其他音频流**全部无响应。
>
> 触发条件很现实：并发上限 `max_concurrent_requests=1`，
> 只要一个请求在合成，第二个流式请求进来就会阻塞在事件循环里等 60 秒。

---

## 一、隐患清单

| # | 隐患 | 位置 | 严重度 | 状态 |
|:-:|---|---|:-:|:-:|
| 1 | `async` 里阻塞等信号量 → 冻结事件循环 60s | `routes/synthesis.py:332` | **P0** | ✅ |
| 2 | 绕过调度器队列上限/取消检查/统计 | 同上 | **高** | ✅ |
| 3 | CSV 公式注入（CWE-1236） | `history/repository.py` | **高** | ✅ |
| 4 | 符号链接绕过音频路径校验 | `routes/speakers.py:40` | **中** | ✅ |
| 5 | 嵌套事务假提交 + 锁不可重入 | `db/session.py:58` | **中** | ✅ |
| 6 | 缺少 `token_max_hop_len` 限制 | `inference/cosyvoice_engine.py` | **中** | ✅ |
| 7 | `release` 不同步更新统计、取消集合泄漏 | `routes/synthesis.py` | **低** | ✅ |

### 契约漂移（与 zjy13 基准不符）

| # | 项 | zjy13 基准 | zjy14 原值 | 状态 |
|:-:|---|---|---|:-:|
| D1 | `lead_sec` | **2.0** | 0.8 | ✅ 已对齐 |
| D2 | `FIRMWARE_RING_MS` | 8s（`PLAY_MAX_SECONDS=8`） | 200ms | ✅ 已修正 |
| D3 | 预缓冲概念 | `PREBUF_MS=120` 独立存在 | 与缓冲容量混为一谈 | ✅ 已拆分 |
| D4 | `token_max_hop_len` | **50** | 未限制（默认 100） | ✅ 已补 |

---

## 二、P0 详解：事件循环被阻塞

### 原代码

```python
async def synthesize_stream(request: Request, payload: SynthesisRequest):
    ...
    acquired = sched._sem.acquire(timeout=state.config.queue_timeout_s)  # noqa: SLF001
    if not acquired:
        raise QueueTimeoutError(...)
```

`threading.BoundedSemaphore.acquire()` 是**阻塞调用**。写在 `async def` 里，
意味着等待期间协程不会让出控制权 —— 整个 asyncio 事件循环停摆。

### 三个连带问题

1. **冻结时长 = `queue_timeout_s` = 60 秒**
   这 60 秒内所有 HTTP 请求（含 `/health`）都拿不到响应。
2. **绕过队列上限**
   `Scheduler.run()` 会先检查 `queued >= max_queue` 并抛 `QueueFullError(429)`；
   直连 `_sem` 完全跳过，队列可以无限堆积。
3. **统计失真**
   `active` / `queued` / `total_rejected` 都不更新，
   `/api/v1/system/status` 里的数字与实际严重不符。

### 修复

在 `Scheduler` 里新增一对方法（**不破坏 `run()` 的既有语义**）：

```python
def acquire_slot(self, request_id, *, timeout_s=None) -> None:
    # 队列上限检查 -> 排队（阻塞）-> 统计更新 -> 取消检查
def release_slot(self, request_id, *, run_ms=0.0) -> None:
    # active 递减、total_served 递增、清取消标记、归还信号量
```

路由改为：

```python
await run_in_threadpool(sched.acquire_slot, req_id, timeout_s=...)
...
finally:
    if acquired:
        sched.release_slot(req_id, run_ms=...)
```

阻塞等待被挪到工作线程，事件循环保持可响应。

### 为什么 `acquire_slot` 保持同步

它是阻塞 API，由调用方决定放不放进线程池 —— 这样才灵活
（`generate()` 本身跑在 Starlette 的线程池里，那里直接调是安全的）。
测试里用 `inspect.iscoroutinefunction` 锁住了这个契约。

---

## 三、契约漂移详解

### D1：`lead_sec` 0.8 → 2.0

zjy13 在 2026-11-23 实测发现：

| lead_sec | 板子饥饿时长 |
|---|---|
| 0.8（原） | **1.60s** |
| **2.0（新）** | **0.47s** |

原因：TTS 分块间隔最长约 1.7~2.9 秒，只领先 0.8 秒发送，
上游一停顿板子就播空了。zjy14 的 `BackpressureState` 仍是 0.8。

> 注意：`BackpressureState` 目前**只在测试与诊断脚本里用**
> （生产链路走 `encode_length_prefixed`），所以它不影响实际下发。
> 但保留错误的基准值会误导后续开发 —— 谁把它接进生产就踩坑。

### D2/D3：`FIRMWARE_RING_MS` 200 → 8000

原常量把两个概念混成一个：

| 概念 | 板上真实值 | 作用 |
|---|---|---|
| `PREBUF_MS` | **120ms** | 开播门槛：攒够才出声 |
| `PLAY_MAX_SECONDS` | **8s** | 环形缓冲容量：满了丢新数据 |

原值 200 且命名为 `RING_MS`，让人误以为「固件只有 200ms 缓冲，
多发就会溢出」，进而把 `lead_sec` 压得很小 —— 这恰好加剧了饥饿。

现在拆成 `FIRMWARE_PREBUF_MS = 120` 与 `FIRMWARE_RING_MS = 8000`，
并加了测试锁住「`lead_sec` 必须明显小于 `FIRMWARE_RING_MS`」。

### D4：补上 `token_max_hop_len = 50`

zjy14 的 `_reset_hop_len` 只重置了 `token_hop_len=25`，
但合成过程中它仍会按 ×2 涨到 `token_max_hop_len`（上游默认 **100**）。

zjy13 实测（`scripts/tts_server_wsl.py`）：

| max_hop | 最大间隔 | 饥饿时长 | RTF |
|---|---|---|---|
| 100（默认） | 2.85s | 1.60s | 0.80 |
| **50（采用）** | **1.69s** | **0.47s** | **0.94** |
| 25 | 1.17s | 1.15s | 1.13 ❌ |

**不能降到 25**：RTF 升到 1.13，合成跟不上播放，饥饿反而恶化。

> 这是本次「结合 zjy13 修复成果完善 zjy14」最实质的一条：
> 下游 `encode_length_prefixed` 按 48KB 切块只解决「单块超限」，
> 解决不了「上游一次推理太久」造成的间隔。

---

## 四、安全修复

### 4.1 CSV 公式注入（CWE-1236）

导出 CSV 时 `text` 字段直接写出，而该字段来自 LLM 回复。
若文本以 `=` `+` `-` `@` `\t` `\r` 开头，Excel 打开时会**当公式执行**
（典型 payload：`=cmd|'/c calc'!A1`）。

修复：加 `_csv_safe()`，对这类字符串加前导单引号（Excel 的强制文本标记，
单元格里不显示，不影响可读性）。数字/`None` 原样返回 ——
负数以 `-` 开头但加引号会破坏数据类型。

### 4.2 符号链接绕过路径校验

`_resolve_audio_path` 原来只用 `Path(raw).parts` 判断有没有 `..`。
但 `link.wav -> /etc/shadow` 的 `parts` 里**没有** `..`，校验直接通过，
引擎随后会读真实目标。

修复：先 `resolve(strict=True)` 拿真实路径，再对真实路径判穿越与扩展名。
`strict=True` 同时消除了「先校验、后文件消失」的 TOCTOU。

> 保留原契约：相对路径仍优先报「必须是绝对路径」，
> 对调用方的指导性更强，也不破坏既有测试。

### 4.3 嵌套事务

| 问题 | 后果 | 修复 |
|---|---|---|
| 内层事务结束即 COMMIT | 把外层未完成的工作提前提交，外层出错回滚不掉 | `_tx_depth` 计数，只有最外层 BEGIN/COMMIT/ROLLBACK |
| `threading.Lock` 不可重入 | 嵌套时同一线程自锁，请求永久卡住 | 改用 `RLock` |

服务已启用流式并发，而落库发生在每个流的收尾 —— 写操作确实可能并发。

---

## 五、验证

```
zjy14 测试：517 passed（原 483 + 新增 34）
```

新增文件 `tests/test_health_check_2026_11_25.py`（34 项），覆盖：

| 类别 | 用例 |
|---|---|
| P0 事件循环 | 不得出现 `_sem.acquire`、必须用 `run_in_threadpool`、`acquire_slot` 必须是同步函数 |
| 调度器 | 队列上限检查、排队超时、`active`/`total_served` 统计、反复 acquire/release 不漏槽位 |
| 契约 | `lead_sec≥2.0`、`lead_sec < FIRMWARE_RING_MS`、`RING_MS=8000`、`PREBUF_MS=120`、`FIRST_BURST_MS=300`、`FRAME_BYTES=4096` |
| 引擎 | `_reset_hop_len` 真的同时设置两个属性、`TOKEN_MAX_HOP_LEN=50` |
| CSV | 6 种危险前缀被转义、普通文本不受影响、非字符串原样保留 |
| 路径 | 符号链接逃逸被拒、正常 wav 通过、相对路径报「绝对路径」 |
| 数据库 | 嵌套回滚有效、嵌套提交正常、RLock 不自锁 |

---

## 六、改动清单

| 文件 | 改动 |
|---|---|
| `custom_voice/inference/scheduler.py` | 新增 `acquire_slot()` / `release_slot()`（含队列上限、取消检查、统计） |
| `custom_voice/server/routes/synthesis.py` | 改用 `run_in_threadpool` + `acquire_slot`/`release_slot`；移除直连 `_sem` 与不再使用的 `QueueTimeoutError` 导入 |
| `custom_voice/bridge/protocol.py` | `lead_sec` 0.8→2.0；`FIRMWARE_RING_MS` 200→8000；新增 `FIRMWARE_PREBUF_MS=120` |
| `custom_voice/inference/cosyvoice_engine.py` | 新增 `TOKEN_MAX_HOP_LEN=50`，`_reset_hop_len` 同时限制 `token_max_hop_len` |
| `custom_voice/history/repository.py` | 新增 `_csv_safe()`，CSV 导出行应用 |
| `custom_voice/server/routes/speakers.py` | `_resolve_audio_path` 改用 `resolve(strict=True)` |
| `custom_voice/db/session.py` | `RLock` + `_tx_depth` 嵌套事务 |
| `docs/TROUBLESHOOTING.md` | `lead_sec=0.8`→`2.0`；预缓冲 200ms→120ms |
| `README.md` | 测试数 145 → 517（两处） |
| `tests/test_health_check_2026_11_25.py` | **新增**，34 项回归 |

---

## 七、未做 / 未验证

| # | 项 | 说明 |
|:-:|---|---|
| 1 | 未接入 zjy13 的**流式空闲超时** | zjy13 在 `tts/client.py` 有 20s 空闲超时（调用方侧）。zjy14 作为**被调用方**不需要它；真正的风险在「生成器被取消后后台推理仍在跑」，本次未处理（属资源泄漏，非正确性问题） |
| 2 | 未做真机联调 | 本次只跑单元测试。zjy14 接板子的端到端验证依赖 zjy13 刚修复的固件，建议后续用 `TTS_BASE_URL=:50001` 实测一轮 |
| 3 | `BackpressureState` 仍是测试专用 | 生产链路走 `encode_length_prefixed`，节流由 zjy13 侧负责。本次只对齐了基准值，未把它接进生产（属架构决策，不擅自改） |
| 4 | 队列统计的「排队中」计数 | 流式请求在 `acquire_slot` 期间 `queued` 是递增的，但**跨 await**，若监控恰好在中间采样会看到瞬时排队数 —— 属预期行为 |

---

*本文档所有结论均来自实际读到的代码与实跑的测试结果；未验证项已明确标注。*