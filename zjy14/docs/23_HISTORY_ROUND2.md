# 历史记录与日志：第二轮补齐

> 日期：2026-11-18
> 前置：`docs/22_HISTORY_ENHANCEMENTS.md` 里列出的 4 个仍未做项
> 结果：**全部补齐**，472 个测试全绿（新增 40），端到端 29 项通过

---

## 一、补齐清单

| # | 原限制 | 补齐内容 |
|:-:|---|---|
| 1 | 日志跨文件搜索（需指定单个文件） | `file_name=*` 或 `all` 跨全部文件 |
| 2 | 全文搜索按时间倒序（未按相关度） | `order_by=relevance` 用 bm25 |
| 3 | 导出无进度反馈 | 流式响应 + `x-export-total` 头 |
| 4 | 日志清理未入审计 | 写 `cleanup_logs`，含被删文件名 |

---

## 二、逐项实现

### 2.1 日志跨文件搜索

**原问题**：排查历史问题时经常不知道是哪天的日志。要先查文件列表、
再逐个文件查，效率太低。

**实现**：`file_name` 传 `*` 或 `all` 时遍历所有日志文件。

```python
if q.file_name in ("*", "all"):
    return _query_all_files(q)
```

**关键设计**：

1. **按修改时间倒序**排列文件（当前日志永远最前），逐个查。
   每个文件内部已是最新在前，所以结果整体近似时间倒序。

2. **结果带 `source_file`** —— 否则用户不知道这条日志是哪天的。
   这是必须的：跨文件搜索后，只有时间戳不够定位。

3. **limit 约束总量**，单文件扫描预算不变，不会因为文件多而爆炸。

### 2.2 全文搜索相关度排序

**实现**：`order_by=relevance`，用 FTS5 的 `bm25()`。

```sql
SELECT s.* FROM synthesis_logs s
JOIN (
  SELECT rowid AS rid, bm25(synthesis_fts) AS rank
  FROM synthesis_fts WHERE synthesis_fts MATCH ?
) fts ON s.id = fts.rid
ORDER BY fts.rank ASC, s.created_at DESC
```

#### 踩到的坑：bm25() 不能在 JOIN 外层调用

第一版写成 `bm25(synthesis_fts)` 在外层 SELECT，报
`no such column: synthesis_fts`。

**原因**：`bm25()` 只能引用**直接查 FTS 表的那个 SELECT** 里的表名。
JOIN 之后 `synthesis_fts` 不是可见表名。

**修正**：在子查询里算 rank 并带出来。

#### 相关度排序没有游标

游标基于 `(created_at, id)`，而相关度顺序与时间无关。
返回游标会让调用方翻页时得到错误结果。

因此 `order_by=relevance` 时 `next_cursor` 恒为 `null`，
用 `has_more` 提示还有更多。

#### 实测：bm25 的评分规律

用 `probe/_diag_bm25.py` 实测（trigram 分词器）：

| 文档 | 长度 | bm25 分数 |
|---|---|---|
| `天气天气天气天好` | 8 | **−0.000002** |
| `天气天今天天气天` | 8 | **−0.000002** |
| `天气天 + 无关内容×10` | 43 | −0.000001 |
| `天气天 + x×100` | 103 | −0.000001 |

**规律**：bm25 有长度归一化，短文档得分更高（更负 = 更相关）。
同长度的两个文档得分可能完全相同。

**这影响了测试设计**：不能假设「词频高的排前」。
必须用**长度差异明显**的文档（8 字 vs 43 字）才能观察到排序差异。

#### 自动回退

相关度只在「用了 text_search 且查询 ≥3 字」时生效。
否则（无搜索词、或 2 字查询走 LIKE）自动回退时间排序 ——
LIKE 没有相关度概念，硬套会得到误导性的结果。

### 2.3 导出进度

**原问题**：同步返回，大导出时用户看到"卡住不动"。

**实现**：改 `StreamingResponse`，边查边写。

```python
async def gen():
    it = repo.iter_export(f, fmt=fmt, max_rows=max_rows)
    while True:
        chunk = await run_in_threadpool(_next_or_none, it)
        if chunk is None:
            break
        yield chunk.encode("utf-8")
```

**关键点**：

1. **`iter_export()` 逐页产出**，内存不随行数增长。
   `export()` 保留为「一次性返回」的兼容接口。

2. **`x-export-total` 头**给出预计总行数（先跑 COUNT），
   前端据此显示「正在导出 N 条」。这比字节级进度更准确 ——
   流式响应的 `content-length` 未知。

3. **`x-export-limit` 头**告知上限，前端可提示「共 X 条，只导 Y 条」。

4. **`run_in_threadpool` 逐块取**，避免同步生成器阻塞事件循环
   （这是 Phase 4 踩过的坑，这里主动规避）。

5. **JSON 流式的逗号处理**：第一条前不加逗号，否则解析失败。
   有测试 `test_iter_export_json_multiple_rows` 用 5 条记录验证。

### 2.4 日志清理审计

**原问题**：只审计合成记录清理，删日志文件无迹可查。

**实现**：`record_cleanup()` 通用方法 + `cleanup_logs.detail` 列。

```python
repo.record_cleanup(
    kind="logs",
    cutoff=log_cutoff,
    removed=len(removed_files),
    triggered_by=triggered_by,
    detail={"files": removed_files},
)
```

**为什么要 `detail`**：审计记录"删了 3 个文件"不够，
必须知道**删的是哪 3 个**。事后若发现误删，能定位。

---

## 三、数据库变更

### 3.1 schema v3 → v4

```sql
-- cleanup_logs 加 detail 列（JSON，存被删文件名等）
ALTER TABLE cleanup_logs ADD COLUMN detail TEXT NOT NULL DEFAULT '';
```

### 3.2 迁移机制改进（重要）

**原问题**：`SCHEMA_SQL` 全是 `CREATE ... IF NOT EXISTS`，
对**已存在**的表不会补新列。之前 docstring 里写了
「将来加列必须按版本分支处理」，这次真的遇到了。

**实现**：新增 `ALTER_STATEMENTS` + `_apply_alters()`。

```python
ALTER_STATEMENTS = [
    (4, "ALTER TABLE cleanup_logs ADD COLUMN detail TEXT NOT NULL DEFAULT ''"),
]

def _apply_alters(db, from_version):
    for target, stmt in ALTER_STATEMENTS:
        if from_version >= target:
            continue
        try:
            conn.execute(stmt)
        except Exception as exc:
            # SQLite 的 ADD COLUMN 重复执行会报 duplicate column
            if "duplicate column" in str(exc).lower():
                continue   # 已存在，跳过
            raise
```

**容错是必须的**：迁移可能中断后重跑，此时列已存在。
不捕获 `duplicate column` 会让服务永久起不来。

有测试 `test_migrate_from_v3_adds_detail_column` 模拟从 v3 升级，
`test_migrate_is_idempotent` 验证重复迁移不报错。

---

## 四、验收结果

### 4.1 端到端（29 项全通过）

```
[1] 全文搜索相关度排序
    order_by=time -> 200                    PASS
    时间排序命中 2 条                        PASS
    order_by=relevance -> 200               PASS
    相关度排序命中数一致（2）                PASS
    相关度排序不返回时间游标                 PASS
    相关度与时间排序返回相同记录集           PASS
    相关度排序可用                           PASS
    2 字 + relevance 不报错（回退）          PASS
    非法 order_by 被拒绝（422）              PASS
[2] 日志跨文件搜索
    文件列表 -> 200（4 个文件）              PASS
    file_name=* -> 200                      PASS
    跨文件结果不少于单文件                   PASS
    跨文件结果都带 source_file               PASS
    file_name=all -> 200                    PASS
[3] 导出进度
    导出 CSV -> 200                         PASS
    含 x-export-total（进度用）              PASS
    含 x-export-limit（上限提示）            PASS
    x-export-total=3                        PASS
    CSV 带 BOM                              PASS
    导出 JSON 限 2 行                        PASS
    max_rows 生效                           PASS
    x-export-total 不受 max_rows 影响        PASS
[4] 清理审计
    清理 -> 204                             PASS
    审计 -> 200                             PASS
    审计记录含 detail 字段                   PASS
    detail 是 dict                          PASS
    类型正确（synthesis）                    PASS
```

### 4.2 测试

```
472 个全绿（原 432 + 新增 40）
```

新增 `tests/test_history_round2.py` 40 个：

| 类别 | 数量 | 覆盖 |
|---|---|---|
| 跨文件搜索 | 10 | `*`/`all`、source_file、limit、级别、关键词、当前优先 |
| 相关度排序 | 7 | bm25 顺序、无搜索回退、短查询回退、无游标、组合筛选 |
| 流式导出 | 10 | CSV/JSON、多条逗号、空结果、max_rows、跨页、与 export 等价 |
| 清理审计 | 6 | logs 类型、detail 解析、空 detail、两种类型、调度器集成 |
| 迁移 | 6 | v4、ALTER 定义、幂等、v3→v4 加列、默认值 |

### 4.3 前端

| 项 | 结果 |
|---|---|
| 类型检查 | 通过 |
| 生产构建 | JS 221.33KB（gzip 72.10KB） |
| 新增 | 排序切换、导出进度提示、审计 detail 展示、跨文件搜索选项 |

---

## 五、使用示例

### 5.1 跨文件搜索日志

```bash
# 跨全部文件搜索错误
curl -s "localhost:50001/api/v1/logs?file_name=*&level=ERROR&limit=50"

# 结果里每条带 source_file，能看出是哪天的日志
```

### 5.2 按相关度排序

```bash
# 搜索 + 相关度排序（查询需 ≥3 字）
curl -s "localhost:50001/api/v1/history?text_search=天气真&order_by=relevance"
```

### 5.3 导出（带进度）

```bash
# 看总数
curl -sI "localhost:50001/api/v1/history/export?fmt=csv" | grep x-export

# 下载
curl -OJ "localhost:50001/api/v1/history/export?fmt=csv"
```

### 5.4 查清理审计

```bash
curl -s "localhost:50001/api/v1/history/cleanup-history" | python3 -m json.tool
```

返回示例：

```json
[
  {
    "kind": "logs",
    "cutoff": "2026-11-04T01:00:00+00:00",
    "removed": 2,
    "triggered_by": "scheduled",
    "detail": {"files": ["zjy14.log.2026-10-01", "zjy14.log.2026-10-02"]},
    "created_at": "2026-11-18T01:00:05+00:00"
  }
]
```

---

## 六、经验教训

1. **`bm25()` 必须在直接查 FTS 表的 SELECT 里调用**。放在 JOIN 外层会报
   `no such column`。这是 FTS5 的表名可见性规则，不查文档会踩。

2. **bm25 有长度归一化，测试不能假设"词频高排前"**。实测同长度文档
   得分可能完全相同。要观察排序差异，必须用长度差明显的文档。

3. **相关度排序不能复用时间游标**。顺序与游标键无关，复用会翻错页。
   正确做法是明确「这种排序不支持游标」。

4. **`ALTER TABLE ADD COLUMN` 必须容错**。迁移中断后重跑会撞
   `duplicate column`，不捕获会让服务永久起不来。

5. **审计要记「删了什么」而不只是「删了几个」**。事后定位误删时，
   文件名是唯一线索。

6. **流式 JSON 的逗号是最容易错的地方**。第一条前不能有逗号，
   否则整份文件解析失败。必须有「多条记录」的测试。

---

## 七、复现命令

```bash
# 第二轮补齐测试
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && source ~/cosyvoice-venv/bin/activate && \
  PYTHONPATH=. python -m pytest tests/test_history_round2.py -o addopts='' -q"

# 全套测试
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/run_tests.sh

# 端到端（需先启动服务）
wsl -e bash probe/run_d1.sh probe/_p11_round2_e2e.py http://127.0.0.1:50014

# 前端构建
cd web && npm.cmd run build
```
