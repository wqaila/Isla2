# 历史记录与日志：四个限制的补齐

> 日期：2026-11-18
> 前置：`docs/21_HISTORY_LOGS_API.md` 里列出的 4 个已知限制
> 结果：**全部补齐**，432 个测试全绿（新增 58），端到端 32 项通过

---

## 一、补齐清单

| # | 原限制 | 补齐内容 |
|:-:|---|---|
| 1 | 日志只读当前文件，归档只列不查 | `file_name` 参数可查任意归档 |
| 2 | 日志级别精确匹配（WARN 匹配不到 WARNING） | 前缀匹配 + 大小写不敏感 |
| 3 | 无导出 | `GET /history/export?fmt=csv\|json` |
| 4 | 全文搜索无索引（LIKE 全表扫） | FTS5 trigram 索引 + 自动降级 |
| 5 | 无自动清理 | 后台线程按保留期清理 + 审计表 |

> 第 5 项是原列表里"无自动清理"的补齐，一并做了。

---

## 二、逐项实现

### 2.1 归档日志查询

**原问题**：`/api/v1/logs` 只能读 `zjy14.log`，`/logs/files` 列出了归档
却没法查内容。

**实现**：`LogQuery.file_name` + `log_file_path(file_name)`。

**安全校验**（必须做，否则等于任意文件读取）：

```python
if "/" in name or "\\" in name or ".." in name:
    raise ValueError(f"非法日志文件名：{file_name!r}")
if not name.startswith(LOG_FILENAME):
    raise ValueError(...)
```

拒绝：`../etc/passwd`、`zjy14.log/../../x`、`other.log`。
测试 `test_log_file_path_rejects_traversal` + `test_http_logs_archive_traversal_rejected` 守护。

### 2.2 日志级别模糊匹配

**原问题**：`?level=WARN` 返回 0 条却不报错，容易误判"没有警告"。
这又是一种静默失效。

**实现**：前缀匹配 + 大小写不敏感。

```python
def _level_matches(entry_level: str, wanted: list[str]) -> bool:
    if not wanted:
        return True
    lv = entry_level.upper()
    return any(lv == w or lv.startswith(w) for w in wanted)
```

现在 `WARN` / `warn` / `WARNING` 都能匹配 `WARNING`；
`ERR` 匹配 `ERROR`；`CRIT` 匹配 `CRITICAL`。

### 2.3 导出 CSV / JSON

**实现**：`GET /api/v1/history/export`，支持与列表相同的全部筛选参数。

**关键点**：

1. **CSV 带 UTF-8 BOM**（`\ufeff`）—— 否则 Excel 打开中文乱码。
   有测试 `test_export_csv_has_bom` 守护。

2. **内部跨页取全量** —— 导出常需"当前筛选下的全部数据"，
   不能只取第一页。实现里循环用游标翻页直到取满 `max_rows`。
   有测试 `test_export_paginates_internally`（造 250 条，超过单页上限 200）。

3. **`max_rows` 上限** `EXPORT_MAX_ROWS = 100_000`，防止拉爆内存。

4. **`content-disposition`** 让浏览器触发下载，文件名带时间戳。

前端加了「导出 CSV」「导出 JSON」两个按钮，自动带上当前筛选条件。

### 2.4 全文搜索（FTS5）

这是本次最需要谨慎的一项，因为**中文分词有坑**。

#### 踩到的坑：unicode61 对中文无效

第一版用 FTS5 默认的 `unicode61` 分词器，结果：

```
数据: '今天天气不错'
搜 '天气'   -> 0 条
搜 '天*'    -> 0 条
搜 整句     -> 1 条
```

**原因**：unicode61 按 Unicode 词边界切分，把「今天天气不错」当成
**一个完整 token**。它不是子串搜索，只有搜整句才能命中。

如果不实测就上线，「搜天气返回 0 条」会是一个新的静默失效。

#### 修正：改用 trigram

`trigram` 按 3 字符滑窗建索引，支持子串搜索。实测：

| 查询 | unicode61 | trigram |
|---|---|---|
| `天气`（2 字） | 0 | **0** |
| `今天天`（3 字） | 0 | **1** |
| `今天天气`（4 字） | 0 | **1** |
| 整句 | 1 | 1 |

**trigram 的约束**：查询串必须 >= 3 字符。

#### 自动降级策略

不能让调用方自己判断长度，否则又是一个坑。实现里自动处理：

```python
FTS_MIN_QUERY_LEN = 3

if f.text_search:
    term = f.text_search.strip()
    if len(term) >= FTS_MIN_QUERY_LEN:
        # 走 FTS5 索引
        where.append("id IN (SELECT rowid FROM synthesis_fts WHERE synthesis_fts MATCH ?)")
        params.append(_fts_query(term))
    else:
        # 短词降级 LIKE，保证正确性
        where.append("(text LIKE ? OR instruction LIKE ?)")
        params.extend([f"%{term}%", f"%{term}%"])
```

#### FTS5 特殊字符转义

FTS5 有查询语法（`*` `"` `-` `AND` `NEAR` 等），直接传用户输入会抛
`syntax error`。例如搜 `C++` 或 `"未闭合引号` 都会炸。

```python
def _fts_query(raw: str) -> str:
    tokens = [t for t in raw.split() if t]
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    return " ".join(quoted)
```

每个词用双引号包起来，引号内的特殊字符失去语义。
有测试 `test_fts_search_special_chars` 覆盖 6 种危险输入。

#### 索引同步

用 external content 表 + 3 个触发器（insert / delete / update），
避免数据重复存储。

`rebuild_fts()` 在启动时调用一次，把 v2 升级上来的旧记录补进索引。

### 2.5 自动清理 + 审计

**实现**：`CleanupScheduler` 后台线程。

| 配置项 | 默认 | 说明 |
|---|---|---|
| `history_retain_days` | 30 | 合成记录保留天数，0 表示不清理 |
| `log_retain_days` | 14 | 归档日志保留天数 |
| `cleanup_interval_hours` | 24 | 清理间隔 |

**关键设计**：

1. **启动后延迟 5 秒再跑第一次**，避免与模型加载抢 IO。
2. **用 `Event.wait` 而非 `sleep`**，停止时能及时响应。
3. **失败不影响服务**：清理出错只记日志。
4. **绝不删当前日志**：只删 `zjy14.log.YYYY-MM-DD` 形式的归档。
   有测试 `test_purge_archived_logs_never_touches_current`（把当前日志
   的 mtime 设成 999 天前，仍不能删）。
5. **审计**：每次清理写 `cleanup_logs` 表（kind / cutoff / removed /
   triggered_by / created_at）。

**为什么要审计**：清理是破坏性操作。事后能查「谁在什么时候删了多少」，
比只能看日志可靠。

`/system/status` 新增 `cleanup` 字段暴露运行状态。

---

## 三、数据库变更

### 3.1 schema v2 → v3

新增：

```sql
-- 全文索引（external content，不重复存数据）
CREATE VIRTUAL TABLE synthesis_fts USING fts5(
    text, instruction,
    content='synthesis_logs', content_rowid='id',
    tokenize='trigram'
);

-- 3 个同步触发器
CREATE TRIGGER synthesis_fts_ai AFTER INSERT ON synthesis_logs ...
CREATE TRIGGER synthesis_fts_ad AFTER DELETE ON synthesis_logs ...
CREATE TRIGGER synthesis_fts_au AFTER UPDATE ON synthesis_logs ...

-- 清理审计
CREATE TABLE cleanup_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    cutoff TEXT NOT NULL,
    removed INTEGER NOT NULL,
    triggered_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
```

### 3.2 迁移注意

`SCHEMA_SQL` 全是 `IF NOT EXISTS`，从 v2 升 v3 只需重跑脚本 + 补记版本号。
但**已存在的记录不在新 FTS 表里**，所以启动时会调 `rebuild_fts()`。

---

## 四、验收结果

### 4.1 端到端（32 项全通过）

```
[准备] 合成 3 次建立可搜索数据          PASS
[1] 全文搜索
    3+ 字查询 -> 200                     PASS
    搜「今天天气」命中 1 条              PASS
    搜「天气」(2字，自动降级 LIKE) 命中 1 条  PASS
    特殊字符 C++ / %_ / "quote 不报错    PASS × 3
[2] 日志级别模糊匹配
    level=WARNING -> 52 条               PASS
    level=WARN 与 WARNING 结果相同       PASS
    小写 warn 也匹配                     PASS
[3] 归档日志查询
    文件列表 -> 200                      PASS
    发现 4 个文件（含 3 个归档）         PASS
    查归档 zjy14.log.2026-11-16 -> 100 条  PASS
    路径穿越被拒绝（400）                PASS
    非法文件名被拒绝（400）              PASS
[4] 导出
    导出 CSV -> 200                      PASS
    CSV 带 UTF-8 BOM                     PASS
    CSV 有表头 + 数据行                  PASS
    表头字段正确                         PASS
    触发下载（attachment）               PASS
    导出 JSON -> 200                     PASS
    JSON 结构正确                        PASS
    导出遵守筛选条件                     PASS
    非法格式被拒绝（422）                PASS
[5] 自动清理与审计
    /system/status 暴露清理状态          PASS
    线程状态可读                         PASS
    手动清理 -> 204                      PASS
    审计记录 -> 200                      PASS
    审计类型正确（synthesis）            PASS
    触发方式正确（manual）               PASS
    记录了删除条数（3）                  PASS
    清理后归零（0）                      PASS
```

### 4.2 测试

```
432 个全绿（原 374 + 新增 58）
```

新增 `tests/test_history_enhancements.py` 58 个：

| 类别 | 数量 | 覆盖 |
|---|---|---|
| 归档日志 | 6 | 路径校验、穿越拒绝、归档读取、缺失文件 |
| 级别模糊匹配 | 6 | 精确/前缀/小写/多级别 |
| 导出 | 9 | CSV/JSON、BOM、筛选、分页取全量、上限 |
| 全文搜索 | 13 | 索引存在、3字/2字/1字、特殊字符、同步、重建 |
| 清理审计 | 3 | 写审计、排序、上限 |
| 自动清理 | 8 | cutoff、归档删除、不删当前、0 保留、线程生命周期 |
| HTTP 接口 | 8 | 导出、路由顺序、文本搜索、归档、穿越、审计 |
| 配置 | 4 | 默认值、登记、status 暴露 |

**其中 4 个测试专门守护路由顺序**（`/history/export`、`/history/stats`、
`/history/cleanup-history` 都不能被 `/history/{id}` 吃掉）。

### 4.3 前端

| 项 | 结果 |
|---|---|
| 类型检查 | 通过 |
| 生产构建 | JS 219.65KB（gzip 71.46KB） |
| 新增功能 | 导出按钮、全文搜索、归档选择、清理审计表 |

---

## 五、使用示例

### 5.1 全文搜索

```bash
# 3 字以上走 FTS 索引（快）
curl -s "localhost:50001/api/v1/history?text_search=今天天气"

# 2 字自动降级 LIKE（正确但慢）
curl -s "localhost:50001/api/v1/history?text_search=天气"
```

### 5.2 查归档日志

```bash
# 先看有哪些
curl -s "localhost:50001/api/v1/logs/files"

# 再查具体文件
curl -s "localhost:50001/api/v1/logs?file_name=zjy14.log.2026-11-16&limit=50"
```

### 5.3 级别模糊匹配

```bash
curl -s "localhost:50001/api/v1/logs?level=WARN"    # 匹配 WARNING
curl -s "localhost:50001/api/v1/logs?level=ERR"     # 匹配 ERROR
```

### 5.4 导出

```bash
# CSV（Excel 可直接打开）
curl -OJ "localhost:50001/api/v1/history/export?fmt=csv"

# JSON，带筛选
curl -s "localhost:50001/api/v1/history/export?fmt=json&only_failed=true"
```

### 5.5 清理

```bash
# 手动清理（会写审计）
curl -X DELETE "localhost:50001/api/v1/history?before=2026-10-19T00:00:00%2B00:00"

# 查审计
curl -s "localhost:50001/api/v1/history/cleanup-history"
```

自动清理无需操作，后台线程按 `configs/gpu.yaml` 的保留期执行。

---

## 六、仍未做

| 项 | 说明 |
|---|---|
| 日志跨文件搜索 | 目前需指定单个文件；跨全部归档搜索未做 |
| 全文搜索的排序 | 现在按时间倒序，未按相关度（FTS5 的 `rank`） |
| 导出进度 | 大导出无进度反馈（同步返回） |
| 日志清理的审计 | 只审计合成记录清理，日志文件删除未入表 |

---

## 七、复现命令

```bash
# 增强功能测试
wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy14 && source ~/cosyvoice-venv/bin/activate && \
  PYTHONPATH=. python -m pytest tests/test_history_enhancements.py -o addopts='' -q"

# 全套测试
wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/run_tests.sh

# 端到端（需先启动服务）
wsl -e bash probe/run_d1.sh probe/_p10_enhancements_e2e.py http://127.0.0.1:50013

# 分词器行为验证
wsl -e bash probe/run_d1.sh probe/_check_trigram_exact.py

# 前端构建
cd web && npm.cmd run build
```

---

## 八、经验教训

1. **中文全文搜索不能想当然**。unicode61 对中文完全无效（把整句当一个
   token），必须实测。如果直接上线，「搜天气返回 0 条」会是新的静默失效。

2. **trigram 有长度下限，必须自动降级**。要求调用方自己判断「查询是否
   ≥3 字符」是不合理的，会变成使用者踩坑。降级逻辑应在实现内部。

3. **FTS5 特殊字符必须转义**。`C++`、`"` 这类输入会让 SQL 抛异常。
   用户不会知道这是 FTS 语法问题，只会觉得"搜索坏了"。

4. **删除操作必须有审计**。清理是破坏性的，事后能查「删了什么」比
   只能翻日志可靠得多。

5. **测试要用自己刚造的数据**。第一次跑端到端时，搜索词「今天天气」
   被上一轮的清理删掉了，导致误判「FTS 不工作」。测试数据必须自给自足。

6. **路径参数必须有白名单校验**。`file_name` 不加校验等于任意文件读取，
   这类漏洞在"方便调试"的接口里最容易出现。
