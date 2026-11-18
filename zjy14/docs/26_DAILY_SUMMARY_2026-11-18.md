# 2026-11-18 工作总结：成果、不足与缺陷

> 日期：2026-11-18
> 范围：合成记录与日志查询 API（三轮）+ 设备状态监控 Web 端
> 提交：5 个（`b959780` → `eff9ef9`）
> 变更：77 个文件，**+11,192 / −379** 行
> 原则：**只写实测数据，未验证项明确标注**

---

## 一、今日概览

### 1.1 提交时间线

| 时间 | 提交 | 内容 | 变更 |
|---|---|---|---|
| 09:14 | `b959780` | 合成记录与日志查询 API + 前端两页 | 24 文件 +3765/−177 |
| 09:25 | `b4dc742` | 补齐四个限制（FTS/导出/归档/自动清理） | 22 文件 +2331/−36 |
| 09:38 | `96ef69b` | 第二轮补齐（跨文件/相关度/进度/审计） | 16 文件 +1706/−111 |
| 09:44 | `7b59fd5` | 设备监控 Web 端开发提示词（文档） | 2 文件 +586 |
| 10:03 | `eff9ef9` | 设备状态监控 Web 端（zjy13 + zjy14） | 13 文件 +2804/−55 |

**合计 5 个提交，77 个文件，+11,192 / −379 行。**

### 1.2 测试与构建

| 项 | 结果 |
|---|---|
| zjy13 测试 | **521 passed, 1 skipped** |
| zjy14 测试 | **472 passed** |
| zjy13 新增测试 | **37 个**（`test_ws_snapshot.py`） |
| zjy14 新增测试 | **110 个**（history_api 68 + enhancements 42） |
| 前端构建 | JS 229.52KB（gzip 74.92KB） |
| 前端类型检查 | 通过（`tsc --noEmit` 零错误） |

### 1.3 端到端验收

| 脚本 | 项数 | 结果 |
|---|---|---|
| `_p9_history_e2e.py` | 41 | 全通过 |
| `_p10_enhancements_e2e.py` | 32 | 全通过 |
| `_p11_round2_e2e.py` | 29 | 全通过 |
| `verify_device_monitor.py` | 25 | 全通过 |
| `verify_device_monitor_e2e.py` | 47 | 全通过 |
| **合计** | **174** | **全通过** |

---

## 二、成果

### 2.1 合成记录与日志查询 API（三轮迭代）

#### 第一轮：基础查询（`b959780`）

**7 个新接口**：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/history` | 分页查询（游标分页） |
| GET | `/api/v1/history/stats` | 统计（成功/失败、RTF、分组） |
| GET | `/api/v1/history/{id}` | 单条详情 |
| GET | `/api/v1/history/by-request/{rid}` | 按 request_id 查 |
| DELETE | `/api/v1/history?before=` | 按时间清理 |
| GET | `/api/v1/logs` | 日志查询（解析 JSON Lines） |
| GET | `/api/v1/logs/files` | 日志文件列表 |

**关键设计：游标分页而非 OFFSET**

合成记录持续写入，OFFSET 分页会**重复或跳过**行。用 `(created_at, id)` 复合游标：

```sql
WHERE created_at < ? OR (created_at = ? AND id < ?)
ORDER BY created_at DESC, id DESC
```

同一秒内多条记录也能正确翻页（靠 id 兜底）。有测试 `test_pagination_same_timestamp` 守护。

**日志反向读取**：从文件尾部按 64KB 块回读，只解析需要的行数。
坏行跳过并计数（`bad_lines`），不因一行坏了整体失败。

#### 第二轮：补齐四个限制（`b4dc742`）

| 原限制 | 补齐 |
|---|---|
| 日志只读当前文件 | `file_name` 参数可查任意归档 |
| 级别精确匹配 | 前缀匹配（`WARN` → `WARNING`） |
| 无导出 | `GET /history/export?fmt=csv\|json` |
| 全文搜索无索引 | FTS5 trigram + 自动降级 |
| 无自动清理 | 后台线程 + 审计表 |

**中文全文搜索是本轮最难的部分**，详见第三节缺陷 1。

**导出三个要点**：

1. CSV 带 UTF-8 BOM（否则 Excel 中文乱码）
2. 内部跨页取全量（导出常需「当前筛选下全部数据」，不能只取第一页）
3. `max_rows` 上限 10 万，防止拉爆内存

#### 第三轮：补齐四个小缺口（`96ef69b`）

| 原限制 | 补齐 |
|---|---|
| 日志需指定单个文件 | `file_name=*` 跨全部文件 |
| 全文搜索只按时间倒序 | `order_by=relevance` 用 bm25 |
| 导出无进度反馈 | 流式响应 + `x-export-total` 头 |
| 日志清理未入审计 | `cleanup_logs.detail` 记文件名 |

**跨文件搜索的关键**：结果带 `source_file`，否则跨文件搜索后不知道是哪天的日志。

**相关度排序的关键**：不返回时间游标。游标基于 `(created_at, id)`，
而相关度顺序与时间无关，返回游标会让翻页得到错误结果。

**审计记「删了什么」而不只是「删了几个」**：`detail: {"files": [...]}`。
事后若发现误删，文件名是唯一线索。

### 2.2 设备状态监控 Web 端（`eff9ef9`）

#### 先回答一个问题

用户问「有没有做过板子状态检测 Web 端」。核实后回答：**没有**。
原 `DevicesPage.tsx` 只是静态占位页（展示协议约定文字，无任何实时能力）。

#### zjy13 侧：只读数据出口

**`snapshot()` 的四条硬约束**（写在 docstring，不只是口头）：

```
1. 只读：不修改任何状态，不创建任务，不发送消息
2. 不阻塞：纯内存读取，无 IO、无 await
3. 不抛异常：单个 client 出错不能影响其它 client
4. 字段缺失给默认值，不用 None 冒充 0
```

第 3 条有专门测试：故意让一个 client 的 `state` 属性抛异常，另一个仍正常返回。

**HTTP 端点复用 WS 端口**（用 `websockets.serve` 的 `process_request` 钩子）：

```
GET /api/devices          完整快照
GET /api/devices/health   极简探活
GET /api/devices/stream   返回 501（明确不支持 SSE）
其它                      404 + 可用路径列表
非 /api/devices 开头      返回 None → 交给 WS 握手
```

**各阶段计时接入**：ASR / LLM 首字 / TTS 首块 / 播放 四处打点；
`send_audio` 累计帧数；`send_error` 记录错误 + 计数；`_finish_turn` 累计轮次。

#### zjy14 侧：实时页面

**8 种状态徽章**配色与 `states.py` 枚举一一对应：

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

**5 类异常态判定**（全部基于源码常量，不是拍脑袋）：

| 异常 | 判定依据 |
|---|---|
| 心跳超时 | `heartbeat_ok === false`（> 30s `HEARTBEAT_TIMEOUT_S`） |
| 状态卡住 | `state_elapsed_ms > 60000`（`TURN_TIMEOUT_S`） |
| 连接数满 | `active >= max_connections` |
| 阶段偏慢 | ASR>1000 / LLM首字>1500 / TTS首块>3000ms |
| 监控不可达 | 请求失败 + 排查指引 |

**流式的特殊处理**：`expect_frames=0` 时 `progress` 是 `null`，
进度条显示半透明满格。如果显示 0%，会让人以为卡住了。

**轮询 hook 的四个细节**：

```typescript
1. 页面不可见时暂停（document.visibilityState）
2. 切回前台立即刷新一次（不等下一个间隔）
3. 请求失败保留上一次数据（UI 标记「可能过期」）
4. 组件卸载时中止进行中的请求（AbortController）
```

### 2.3 端到端实测

```
active=3  CORS=*
  esp32s3-01  playing    frames=98/134
  esp32s3-02  idle       hb=86.6s（超时，前端红徽章）
  esp32s3-03  recording  frames=17/0（流式）
```

3 块模拟板子覆盖 3 种状态，前端跨端口读取正常。

---

## 三、缺陷

### 3.1 中文全文搜索：unicode61 完全无效（最严重的发现）

**第一版实现**用 FTS5 默认的 `unicode61` 分词器，实测结果：

```
数据: '今天天气不错'
搜 '天气'   -> 0 条
搜 '天*'    -> 0 条
搜 整句     -> 1 条
```

**原因**：unicode61 按 Unicode 词边界切分，把「今天天气不错」当成**一个完整 token**。
它不是子串搜索，只有搜整句才能命中。

**如果不实测就上线，「搜天气返回 0 条」会是一个新的静默失效** ——
这正是这个项目一直在防的缺陷类型。

**修正**：改用 `trigram`（3 字符滑窗）。实测：

| 查询 | unicode61 | trigram |
|---|---|---|
| `天气`（2 字） | 0 | **0** |
| `今天天`（3 字） | 0 | **1** |
| `今天天气`（4 字） | 0 | **1** |

**trigram 的约束**：查询串必须 ≥ 3 字符。

**自动降级**（不能让调用方自己判断长度，否则又变成坑）：

```python
FTS_MIN_QUERY_LEN = 3

if len(term) >= FTS_MIN_QUERY_LEN:
    # 走 FTS5 索引
else:
    # 降级 LIKE（慢但正确）
```

### 3.2 未知音色被静默替换（一直存在，本次才发现）

**发现路径**：做查询 API 时，通过「失败记录能不能查到」发现真实引擎
对不存在的 `voice_id` 返回 **HTTP 200**，实际用默认音色；
而 StubEngine 返回 400。

```python
# 修复前
def _pick_voice(self, voice_id):
    if voice_id and voice_id in self._voices:
        return voice_id
    return self._config.default_voice_id   # 静默替换
```

**这是典型静默失效**：调用方以为在用「角色A」，听到的是「角色B」，
且 HTTP 返回 200。

**修复后暴露第二个问题**：`SynthesisRequest.voice_id` 默认值是 `elysia`
（契约要求不能改），但引擎可能只有 `testvoice`，导致「没传」被误判成
「传了不存在的」。

**正解**：用 Pydantic 的 `model_fields_set` 区分：

| 情况 | 行为 |
|---|---|
| 显式传了存在的音色 | 用它 |
| **显式传了不存在的** | **报 400** |
| 没传（拿到 Schema 默认值） | 用引擎配置的默认音色 |

### 3.3 预热用配置音色导致启动失败

修完 3.2 后，19 个测试 fixture 报错：

```
InvalidRequestError: 未知音色 'elysia'，可用: ['testvoice']
```

**根因**：`_warmup()` 用 `self._config.default_voice_id` 取音色，
但该值可能不在当前音色集合里。

**修复**：预热只需任意可用音色，改为 `next(iter(self._voices))`。

### 3.4 日志脱敏只对嵌套字典生效

```python
# 修复前：只对「值」递归，顶层 api_key 原样返回
fields = {k: _redact(v) for k, v in obj.items() if k not in known}
```

`_redact` 内部只在遇到 dict 时才检查 key，顶层的 `api_key` / `auth_token`
直接透传。

**修复**：对每个顶层字段单独判断 key 名。
有测试 `test_query_logs_redacts_secrets` 守护。

### 3.5 统计 SQL 拼接错误

无筛选条件时，`f"{base} AND error_code = ''"` 会产生：

```sql
FROM synthesis_logs AND error_code = ''   -- 语法错误
```

**修复**：根据是否已有 WHERE 决定用 `AND` 还是 `WHERE`。

### 3.6 RTF 中位数算错

用总记录数定位中间行，但失败记录的 `rtf` 为 0，会把中位数拉低。

**修复**：用成功记录数定位，奇数取 1 行、偶数取 2 行求平均。

### 3.7 日志行号偏移

文件以换行结尾时，`split(b"\n")` 在末尾产生空元素，
早期版本把它当成一行，导致行号整体偏移一位。

**修复**：只在处理尾部第一块时丢弃该空元素。

### 3.8 `bm25()` 不能在 JOIN 外层调用

相关度排序第一版写成外层 `bm25(synthesis_fts)`，报 `no such column`。

**原因**：`bm25()` 只能引用**直接查 FTS 表的那个 SELECT** 里的表名，
JOIN 之后 `synthesis_fts` 不是可见表名。

**修复**：在子查询里算 rank 并带出来。

### 3.9 bm25 的评分规律与直觉不符

实测（trigram 分词）：

| 文档 | 长度 | bm25 |
|---|---|---|
| `天气天气天气天好` | 8 | **−0.000002** |
| `天气天今天天气天` | 8 | **−0.000002** |
| `天气天+无关内容×10` | 43 | −0.000001 |
| `天气天+xxx` | 103 | −0.000001 |

**同长度文档得分完全相同。** 所以「词频高就排前」这个假设是错的。
测试必须用**长度差明显**的文档才能观察到排序差异。

我最初就是按「词频高排前」写的测试，失败后才发现这个规律。

### 3.10 `process_request` 拿不到 HTTP 方法

设备监控第一版写了 405 防 POST：

```python
method = request_headers.get("Method") or request_headers.get("method", "")
if method and method.upper() != "GET":
    return (405, ...)
```

**实测发现**：websockets 12 的 `Headers` 对象**只含 HTTP 头，不含请求方法**。
`method` 恒为空，405 分支永远不触发。

**这比不写更糟** —— 代码看起来在防 POST，实际没防。

**修复**：删掉该逻辑，在 docstring 明确说明限制。
端点本身不提供任何写操作，POST 到只读端点也无副作用，所以可接受。
但**不能声称「只支持 GET」**（那是没验证过的说法）。

### 3.11 urllib 在同一事件循环里请求自己会死锁

设备监控验证脚本第一版用 `urllib.request.urlopen` 请求自己刚启动的 `serve()`：

```
[FAIL] 快照请求失败: TimeoutError: timed out
```

**原因**：`urllib` 是同步阻塞的。它运行在 `serve()` 所在的事件循环里，
请求发出后事件循环被阻塞，`serve()` 无法处理这个请求，互相等待。

**这个坑很隐蔽**：单元测试全过（直接调 `process_request`），
只有真实 HTTP 请求才暴露。

**修复**：用 `asyncio.open_connection` 手写 HTTP 请求。

### 3.12 协议要求先 device_hello

验证 WebSocket 握手时直接发 `heartbeat`，收到的是 `error` 而非 `heartbeat_ack`。

**原因**：协议要求先 `device_hello` 完成握手，否则其它消息会被拒（`not_hello`）。

**修复**：验证脚本先发 `device_hello` 收 `server_hello`，再发心跳。

### 3.13 环境缺 python-dotenv

zjy13 起不来：

```
ModuleNotFoundError: No module named 'dotenv'
```

**原因**：`cosyvoice-venv` 没装 `python-dotenv`。

**修复**：`pip install python-dotenv`。
装完后 zjy13 之前 2 个 `test_config` 失败也一起消失了。

### 3.14 迁移机制改进（这次真的用上了）

之前 `migrate()` 的 docstring 写着「将来给已有表加列必须按版本分支处理」，
这次加 `cleanup_logs.detail` 列就遇到了。

**实现**：新增 `ALTER_STATEMENTS` + `_apply_alters()`：

```python
try:
    conn.execute(stmt)
except Exception as exc:
    if "duplicate column" in str(exc).lower():
        continue   # 已存在，跳过
    raise
```

**容错是必须的**：迁移中断后重跑会撞 `duplicate column`，
不捕获会让服务永久起不来。

### 3.15 前端组件能力不足

设备监控页需要「卡片标题带状态徽章」和「指标下方加说明文字」，
但原 `Card.title` 只接受 `string`，`Metric` 没有 `hint`。

**修复**：扩展组件而不是绕过 —— `Card.title` 改为 `ReactNode`，
`Metric` 加 `hint?: ReactNode`。这两个能力对监控页确实必要。

---

## 四、不足

### 4.1 未完成的功能

| 项 | 状态 | 说明 |
|---|---|---|
| **设备事件流** | ❌ 未做 | 提示词里列了「协议消息流水」（asr_final / playback_start 等），需要 zjy13 额外维护环形缓冲 |
| 设备监控 SSE | ❌ 未做 | `process_request` 无法流式，明确返回 501 |
| 导出进度百分比 | ⚠️ 部分 | 只给总行数（`x-export-total`），不给已导行数（流式响应无法回传中间状态） |
| 全文搜索高亮 | ❌ 未做 | 返回结果不含匹配片段 |
| 日志跨文件时间排序 | ⚠️ 近似 | 按文件修改时间倒序拼接，跨文件边界处可能有轻微乱序 |

### 4.2 目标与现实的差距

| 目标 | 实际 | 差距原因 |
|---|---|---|
| SSE 实时推送 | 轮询 1 秒 | `process_request` 只能返回一次性完整响应 |
| HTTP 方法校验 | 无法实现 | websockets 12 的钩子拿不到方法 |
| 精确的相关度排序 | bm25 近似 | trigram 分词下词频统计不精确，同长度文档可能同分 |

### 4.3 方法上的不足

1. **设备监控验证脚本第一版踩了死锁**（urllib 在事件循环内）。
   应该一开始就用 `asyncio.open_connection`。

2. **相关度排序测试假设错了**（以为「词频高排前」）。
   实测发现 bm25 有长度归一化，同长度文档可能同分。
   测试必须用长度差明显的文档。

3. **405 逻辑写了但从未验证**。如果当时跑一次真实 POST 请求，
   就会发现 `method` 恒为空。**没验证过的防护等于没有防护。**

4. **第一次跑端到端时搜索词被清理删掉了**，误判「FTS 不工作」。
   测试数据必须自给自足，不能依赖环境里已有的。

### 4.4 工程债

| 项 | 说明 |
|---|---|
| `pip freeze` 环境锁 | 仍未做 |
| zjy13 复用 `cosyvoice-venv` | 环境耦合，本次又发现缺 `python-dotenv` |
| 前端 `VITE_ZJY13_BASE` 硬编码默认值 | 换机器要改 `.env.local` |
| 设备监控计数器不持久化 | 重启清零（设计如此，但要文档说明） |

---

## 五、方法论沉淀

### 5.1 有效做法

| 做法 | 效果 |
|---|---|
| **先写提示词再实现** | 设备监控先出 585 行提示词（含所有常量核对），实现时一次成型 |
| **实测分词器行为** | 发现 unicode61 对中文完全无效，避免了新的静默失效 |
| **自动降级** | trigram 的 3 字符限制不能让调用方处理，必须在实现内部消化 |
| **只读快照的四条约束** | 写进 docstring 并逐条测试，避免监控页变成故障源 |
| **异常隔离测试** | 故意让一个 client 抛异常，验证其它不受影响 |
| **数据准确性交叉验证** | 端到端用 3 块板子 3 种状态，逐一核对字段值 |
| **不假装支持** | SSE 返回 501 + 替代方案，而不是假的 `text/event-stream` |

### 5.2 教训

| 教训 | 来源 |
|---|---|
| 中文全文搜索不能想当然 | unicode61 把整句当一个 token |
| 同长度文档 bm25 可能同分 | 相关度测试假设错误 |
| 同步 HTTP 不能在同一事件循环里请求自己 | urllib 死锁 |
| 没验证过的防护等于没有防护 | 405 分支永不触发 |
| 迁移加列必须容错 duplicate column | 中断后重跑会永久起不来 |
| 审计要记「删了什么」 | 只记条数无法定位误删 |
| 测试数据要自给自足 | 搜索词被清理删掉导致误判 |

---

## 六、数据汇总

### 6.1 代码规模

| 项 | 数量 |
|---|---|
| 今日新增/修改文件 | 77 |
| 今日净增行数 | +11,192 / −379 |
| zjy13 新增测试 | 37 |
| zjy14 新增测试 | 110 |
| 今日新增文档 | 5 篇（21–25） |

### 6.2 接口清单（今日新增）

**合成记录与日志（zjy14）**：

```
GET    /api/v1/history                     分页查询
GET    /api/v1/history/stats               统计
GET    /api/v1/history/export              导出 CSV/JSON
GET    /api/v1/history/cleanup-history     清理审计
GET    /api/v1/history/{id}                单条详情
GET    /api/v1/history/by-request/{rid}    按 request_id 查
DELETE /api/v1/history?before=...          按时间清理
GET    /api/v1/logs                        日志查询
GET    /api/v1/logs/files                  日志文件列表
```

**设备监控（zjy13）**：

```
GET /api/devices          完整快照
GET /api/devices/health   健康检查
GET /api/devices/stream   501（明确不支持 SSE）
```

### 6.3 数据库变更

| 版本 | 变更 |
|---|---|
| v2 → v3 | FTS5 虚拟表 + 3 个触发器 + `cleanup_logs` 表 |
| v3 → v4 | `cleanup_logs.detail` 列（JSON，存被删文件名） |

**迁移机制改进**：新增 `ALTER_STATEMENTS` + `_apply_alters()`，
支持给已有表加列，并容错 `duplicate column`。

---

## 七、下一步建议

| 优先级 | 任务 | 前置条件 |
|:--:|---|---|
| P0 | 真机联调（看监控页在真实板子上的表现） | **需要 ESP32 硬件** |
| P1 | 设备事件流（协议消息流水） | 需 zjy13 加环形缓冲 |
| P2 | 换引擎评估（情绪控制） | 若情绪是硬需求 |
| P3 | `pip freeze` 环境锁 | 无 |
| P4 | 全文搜索高亮片段 | 无 |

---

## 八、相关文档索引

| 文档 | 内容 |
|---|---|
| `docs/21_HISTORY_LOGS_API.md` | 合成记录与日志 API（第一轮） |
| `docs/22_HISTORY_ENHANCEMENTS.md` | 四个限制补齐（FTS/导出/归档/自动清理） |
| `docs/23_HISTORY_ROUND2.md` | 第二轮补齐（跨文件/相关度/进度/审计） |
| `docs/24_DEVICE_MONITOR_PROMPT.md` | 设备监控开发提示词（585 行） |
| `docs/25_DEVICE_MONITOR_REPORT.md` | 设备监控实现报告 |
