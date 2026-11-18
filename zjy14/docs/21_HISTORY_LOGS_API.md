# 合成记录与日志查询 API

> 日期：2026-11-18
> 交付：6 个新接口 + 前端 2 个页面
> 测试：**374 个全绿**（新增 70 个），端到端 41 项通过

---

## 一、交付内容

### 1.1 新增接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/history` | 合成记录分页查询（游标分页） |
| GET | `/api/v1/history/stats` | 统计（成功/失败、RTF、分组） |
| GET | `/api/v1/history/{id}` | 单条详情 |
| GET | `/api/v1/history/by-request/{rid}` | 按 request_id 查 |
| DELETE | `/api/v1/history?before=` | 按时间清理 |
| GET | `/api/v1/logs` | 日志查询（解析 JSON Lines） |
| GET | `/api/v1/logs/files` | 日志文件列表（含轮转归档） |

### 1.2 前端页面

| 页面 | 路由 | 功能 |
|---|---|---|
| **合成记录**（新增） | `/history` | 统计卡片、多条件筛选、游标翻页 |
| **日志中心**（重写） | `/logs` | 真实日志查询、级别/关键词过滤、文件列表 |

日志中心从「给几条排查命令」升级为「真实查询界面」，
这是本次最直接的用户价值。

---

## 二、关键设计决策

### 2.1 游标分页，不用 OFFSET

**为什么**：合成记录持续写入。OFFSET 分页在数据增长时会**重复或跳过**行 ——
第 2 页可能包含第 1 页已看过的记录。

用 `(created_at, id)` 复合游标：

```sql
WHERE created_at < ? OR (created_at = ? AND id < ?)
ORDER BY created_at DESC, id DESC
```

**同一秒内多条记录**也能正确翻页（靠 id 兜底）。
有专门测试 `test_pagination_same_timestamp` 守护。

游标经过 base64 编码，调用方不必依赖内部格式。

### 2.2 日志反向读取

**为什么**：日志持续追加，「看最近 N 条」是主要场景。
正向读需要扫完整个文件，几十 MB 时会很慢。

实现：从文件尾部按 64KB 块回读，只解析需要的行数。
大文件下仍会做一次全文件行数统计（用于给出行号），但只读不解析。

**容忍坏行**：进程被杀会留下截断行。解析失败的行跳过并计数
（返回 `bad_lines`），不能因为一行坏了就整体失败。

### 2.3 敏感字段双重脱敏

写入时 `core/logging.py` 的 `_redact` 已脱敏，读取时**再兜底一遍**。

这里发现并修复了一个真实 bug（见第三节）。

### 2.4 失败记录也要落库

**为什么**：查询 API 的 `only_failed` 和「失败统计」需要真实数据。
若只记成功，「失败率」永远是 0，指标失去意义。

新增 `_record_failure()`，在合成异常路径写入记录（`error_code` 填异常 code）。

---

## 三、发现并修复的 5 个缺陷

### 3.1 未知音色被静默替换（最严重）

**这是本次最有价值的发现，且不是新代码引入的。**

现象：用真实引擎请求 `voice_id="不存在的音色"`，HTTP 返回 **200**，
实际用的是默认音色。而 StubEngine 一直返回 400。

```python
# 修复前
def _pick_voice(self, voice_id):
    if voice_id and voice_id in self._voices:
        return voice_id
    # 不存在就静默回退 —— 调用方以为在用「角色A」，听到的是「角色B」
    return self._config.default_voice_id
```

**这是典型的静默失效**：不报错、不崩溃，但结果是错的。
正是本项目一直在防的缺陷类型（见迭代史 5.1 节）。

**修复**：显式指定的音色不存在就报 400。

但修复时又暴露第二个问题：

### 3.2 Schema 默认值与引擎音色冲突

`SynthesisRequest.voice_id` 的默认值是 `"elysia"`（为兼容 zjy13 契约，不能改）。
但引擎注册的音色可能不是它（测试环境只有 `testvoice`）。

修复后，不传 `voice_id` 会拿到 Schema 默认值 `elysia`，
引擎却报「未知音色 elysia」——**把「没传」误判成「传了不存在的」**。

**修复**：用 Pydantic 的 `model_fields_set` 区分「显式指定」与「使用默认值」：

```python
voice = self._pick_voice(
    req.voice_id, explicit="voice_id" in req.model_fields_set
)
```

语义变为：

| 情况 | 行为 |
|---|---|
| 显式传了存在的音色 | 用它 |
| 显式传了不存在的音色 | **报 400**（不能静默换声音） |
| 没传（拿到 Schema 默认值） | 用引擎配置的默认音色 |

### 3.3 预热用配置音色，导致启动失败

修完 3.1 后，19 个测试 fixture 报错：

```
InvalidRequestError: 未知音色 'elysia'，可用: ['testvoice']
```

根因：`_warmup()` 用 `self._config.default_voice_id` 取音色，
但该值可能不在当前音色集合里。

**预热只需任意一个可用音色**，不该受配置约束。
改为直接取 `next(iter(self._voices))`。

### 3.4 日志脱敏只对嵌套字典生效

```python
# 修复前：只对「值」递归，顶层的 api_key 原样返回
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

文件以换行结尾时，`split(b"\n")` 会在末尾产生一个空元素。
早期版本把它当成一行，导致行号整体偏移一位。

**修复**：只在处理尾部第一块时丢弃该空元素。

---

## 四、验收结果

### 4.1 端到端（41 项全通过）

```
[1] 合成 3 次（建立数据）              3 次成功
[2] 查询合成记录列表                   8 项字段校验通过
[3] 分页游标不重复                     翻页 111 -> 110
[4] 统计与列表一致                     total=111 rtf中位=0.674
[5] 按 request_id 查                   返回 1 条
[6] 单条详情与 404                     200 / 404 正确
[7] 失败记录可查                       only_failed 查到记录
[8] 真实日志解析                       20 条，0 坏行，中文无乱码
[9] 日志过滤                           级别/request_id 均正确
[10] 日志文件列表                      识别当前与归档
[11] 参数校验                          400/422 正确
[12] 清理接口                          语义正确（不误删新数据）
```

### 4.2 单元测试

```
374 个全绿（原 304 + 新增 70）
```

新增 `tests/test_history_api.py` 68 个 + 引擎音色校验 2 个：

| 类别 | 数量 | 覆盖 |
|---|---|---|
| 游标 | 4 | 编解码、不透明性、非法格式 |
| 仓储查询 | 9 | 各筛选条件、排序 |
| 分页 | 6 | 不重复、has_more、同秒记录、上限截断 |
| 单条查询 | 4 | 按 id、按 request_id、不存在 |
| 统计 | 8 | 计数、时长、RTF、分组、时间范围 |
| 清理 | 2 | 删除与边界 |
| HTTP 接口 | 14 | 各端点、404、路由顺序、参数校验 |
| 日志解析 | 16 | 过滤、坏行、脱敏、行号、文件列表 |
| 日志 HTTP | 7 | 各查询参数 |
| 引擎音色 | 2 | 未知音色报错、未指定用默认 |

**其中一个测试专门守护路由顺序**：

```python
def test_http_history_stats_route_not_shadowed(client):
    """``/history/stats`` 不能被 ``/history/{record_id}`` 吃掉。"""
```

这是 FastAPI 按注册顺序匹配的经典陷阱。

### 4.3 前端

| 项 | 结果 |
|---|---|
| TypeScript 类型检查 | 通过 |
| 生产构建 | JS 216.94KB（gzip 70.54KB） |
| 路由 | `/history` `/logs` 均 200 |
| 通过 Vite proxy 取数 | stats / logs 均正常 |

---

## 五、数据库变更

### 5.1 schema v1 → v2

新增 4 个索引（支持查询场景）：

```sql
CREATE INDEX idx_synthesis_speaker ON synthesis_logs(speaker_id);
CREATE INDEX idx_synthesis_emotion ON synthesis_logs(emotion);
CREATE INDEX idx_synthesis_error   ON synthesis_logs(error_code);
CREATE INDEX idx_synthesis_created_desc ON synthesis_logs(created_at DESC, id DESC);
```

**迁移方式**：`SCHEMA_SQL` 全是 `CREATE ... IF NOT EXISTS`，
从任意旧版本升级都是「重跑建表脚本 + 补记版本号」，无需单独 ALTER。

已在 `migrate()` 的 docstring 里注明：将来若要给已有表加列，
必须按版本分支处理。

### 5.2 写入字段修正

`_record_synthesis` 补上了 `speaker_id`（存 voice_id）。
之前没写这列，导致按音色筛选永远查不到。

---

## 六、架构说明

### 6.1 分层

```
server/routes/history.py     只做参数校验 + 响应组装
schemas/history.py           Pydantic 模型
history/repository.py        合成记录仓储（SQL 集中在此）
history/logs.py              日志文件读取（反向读取 + 解析）
core/errors.py               NotFoundError（从 routes/characters.py 提升）
```

**为什么单独 `history/` 包**：
- 路由层不写 SQL，便于测试与复用
- 日志读取逻辑（反向读、坏行容忍）与业务无关，独立成模块

### 6.2 顺带修正的架构问题

`NotFoundError` 原本定义在 `routes/characters.py` 里，
但合成记录也要用。已提升到 `core/errors.py`。

---

## 七、使用示例

### 7.1 查询最近的合成记录

```bash
curl -s "localhost:50001/api/v1/history?limit=10" | python3 -m json.tool
```

### 7.2 只看失败的

```bash
curl -s "localhost:50001/api/v1/history?only_failed=true"
```

### 7.3 按音色 + 时间范围

```bash
curl -s "localhost:50001/api/v1/history?voice_id=elysia&since=2026-11-18T00:00:00%2B08:00"
```

### 7.4 翻页

```bash
# 第一页
curl -s "localhost:50001/api/v1/history?limit=20" | jq -r .next_cursor
# 第二页（把上一步的游标传回来）
curl -s "localhost:50001/api/v1/history?limit=20&cursor=<游标>"
```

### 7.5 看错误日志

```bash
curl -s "localhost:50001/api/v1/logs?level=ERROR&limit=20" | python3 -m json.tool
```

### 7.6 按 request_id 追踪一次请求

```bash
# 从合成响应头拿到 x-request-id，然后
curl -s "localhost:50001/api/v1/logs?request_id=<rid>"
```

### 7.7 清理 30 天前的记录

```bash
curl -X DELETE "localhost:50001/api/v1/history?before=2026-10-19T00:00:00%2B00:00"
```

---

## 八、已知限制

| 项 | 说明 |
|---|---|
| 日志只读当前文件 | `/api/v1/logs` 读 `zjy14.log`；归档文件只列在 `/logs/files`，未提供按文件名查询 |
| 无全文搜索索引 | 日志关键词是内存中逐行匹配，大文件（>100MB）会慢 |
| 无自动清理 | `DELETE /history` 需手动或外部 cron 调用 |
| 无导出 | 未提供 CSV/JSON 导出 |
| 日志级别过滤是精确匹配 | 传 `WARN` 匹配不到 `WARNING` |

这些都不影响当前使用，按需再加。

---

## 九、复现命令

```bash
# 单元测试
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && source ~/cosyvoice-venv/bin/activate && \
  PYTHONPATH=. python -m pytest tests/test_history_api.py -o addopts='' -q"

# 全套测试
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/run_tests.sh

# 端到端联调（需先启动服务）
wsl -e bash probe/run_d1.sh probe/_p9_history_e2e.py http://127.0.0.1:50011

# Phase 9 验收
wsl -e bash scripts/verify_phase9.sh

# 前端构建
cd web && npm.cmd run build
```

---

## 十、经验教训

1. **新功能会暴露旧缺陷**。这次做查询 API，才通过「失败记录能不能查到」
   发现引擎会静默替换未知音色。这个 bug 一直在，只是没有观测手段。

2. **修复一个缺陷可能引出另一个**。改「未知音色报错」后，
   才暴露「Schema 默认值与引擎音色冲突」。用 `model_fields_set`
   区分「显式指定」与「使用默认值」才是正解。

3. **测试断言要跟着语义改**。原来有测试断言「未知音色回退到默认」，
   那是把缺陷行为固化成了规格。修复时必须同步改断言并写明理由。

4. **脱敏要测**。`_redact` 只对嵌套字典生效这个 bug，
   不写测试根本发现不了 —— 因为日志里通常没有顶层敏感字段。

5. **路由顺序必须有测试**。`/history/stats` 被 `/history/{id}` 吃掉
   是 FastAPI 的经典陷阱，靠人工 review 容易漏。
