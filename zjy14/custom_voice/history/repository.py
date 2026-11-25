"""合成记录仓储：查询、统计、清理。

为什么要单独一层（而不是写在路由里）：
  1. 路由层只做参数校验与响应组装，SQL 集中在仓储，便于测试
  2. 分页/筛选/统计逻辑复用，前端仪表盘与「合成记录」页共用
  3. 架构测试要求 server/ 不碰具体实现，仓储属于可注入依赖

表结构见 ``db/models.py`` 的 ``synthesis_logs``。

设计要点：
  - **分页用游标而非 OFFSET**：记录会持续写入，OFFSET 分页在数据增长时
    会重复或跳过行。这里用 ``(created_at, id)`` 复合游标。
  - **筛选条件全部参数化**：不拼接字符串，避免 SQL 注入。
  - **统计与列表分离**：统计走聚合查询，不受分页影响。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..db.session import Database

#: 单页最大条数。防止调用方一次拉全表。
MAX_PAGE_SIZE = 200

#: 默认页大小
DEFAULT_PAGE_SIZE = 50

#: 导出最大行数。防止一次导出拉爆内存（按每行约 1KB 估算，10 万行约 100MB）。
EXPORT_MAX_ROWS = 100_000

#: CSV 里会触发公式解释的前导字符（CWE-1236 CSV Injection）。
#:
#: 为什么必须处理（2026-11-25 新增）：合成记录的 ``text`` 字段
#: 完全来自调用方（zjy13 传 LLM 回复），若文本以 ``=`` / ``+`` /
#: ``-`` / ``@`` 开头，用 Excel 打开导出的 CSV 时会被当成**公式执行**。
#: 典型攻击是 ``=cmd|'/c calc'!A1`` 这类 DDE 注入。
#:
#: ``\t`` 和 ``\r`` 也要处理：Excel 会先剥掉它们再判断公式。
_CSV_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> Any:
    """给会被 Excel 当公式的单元格加一个前导单引号。

    只处理字符串，非字符串（int/float/None）原样返回 —— 数字
    以 ``-`` 开头是负数，加引号反而会破坏数据类型。

    单引号是 Excel 的「强制文本」标记，在单元格里不显示，
    所以正常文本的可读性不受影响。
    """
    if not isinstance(value, str) or not value:
        return value
    if value.startswith(_CSV_DANGEROUS_PREFIXES):
        return "'" + value
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class SynthesisRecord:
    """一条合成记录。"""

    id: int
    request_id: str
    text: str
    voice_id: str = ""
    character_id: str | None = None
    speaker_id: str | None = None
    emotion: str = ""
    style: str = ""
    speed: float = 1.0
    instruction: str = ""
    sample_rate: int = 0
    duration_s: float = 0.0
    total_ms: float = 0.0
    first_audio_ms: float | None = None
    rtf: float = 0.0
    peak_vram_mb: float | None = None
    engine: str = ""
    error_code: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "text": self.text,
            "voice_id": self.voice_id,
            "character_id": self.character_id,
            "speaker_id": self.speaker_id,
            "emotion": self.emotion,
            "style": self.style,
            "speed": self.speed,
            "instruction": self.instruction,
            "sample_rate": self.sample_rate,
            "duration_s": self.duration_s,
            "total_ms": self.total_ms,
            "first_audio_ms": self.first_audio_ms,
            "rtf": self.rtf,
            "peak_vram_mb": self.peak_vram_mb,
            "engine": self.engine,
            "error_code": self.error_code,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SynthesisRecord":
        keys = row.keys()
        # 合成记录表用 speaker_id 存音色标识（沿用「声音档案」的列名）。
        # 对外 API 叫 voice_id，这里做一次映射；若查询里显式带了 voice_id 列则优先用它。
        voice = _get(row, keys, "voice_id", None)
        if not voice:
            voice = row["speaker_id"] or ""
        return cls(
            id=row["id"],
            request_id=row["request_id"],
            text=row["text"],
            voice_id=voice,
            character_id=row["character_id"],
            speaker_id=row["speaker_id"],
            emotion=row["emotion"] or "",
            style=row["style"] or "",
            speed=row["speed"],
            instruction=row["instruction"] or "",
            sample_rate=row["sample_rate"],
            duration_s=row["duration_s"],
            total_ms=row["total_ms"],
            first_audio_ms=row["first_audio_ms"],
            rtf=row["rtf"],
            peak_vram_mb=row["peak_vram_mb"],
            engine=row["engine"] or "",
            error_code=row["error_code"] or "",
            created_at=row["created_at"],
        )


def _get(row: sqlite3.Row, keys: Any, name: str, default: Any = None) -> Any:
    """安全取列：列不存在时返回默认值。

    用于兼容旧库（尚未迁移到 v2 时没有某些列的场景）。
    """
    return row[name] if name in keys else default


#: FTS5 trigram 分词器要求查询串至少这么长。
#: 实测：2 字查询返回 0，3 字起正常（见 probe/_check_trigram_exact.py）。
#: 短于此值时自动降级到 LIKE。
FTS_MIN_QUERY_LEN = 3


@dataclass(slots=True)
class QueryFilter:
    """合成记录查询条件。所有字段可选。"""

    #: 按音色/声音筛选
    voice_id: str | None = None
    #: 按角色筛选
    character_id: str | None = None
    #: 按情绪标签筛选（元数据，见 D1 结论）
    emotion: str | None = None
    #: 只看失败的（error_code 非空）
    only_failed: bool = False
    #: 只看成功的
    only_success: bool = False
    #: 文本模糊匹配（LIKE，无索引）
    text_contains: str | None = None
    #: 全文搜索（FTS5，走索引）。与 text_contains 互斥，优先用本项。
    text_search: str | None = None
    #: 排序方式：
    #:   "time"    按时间倒序（默认，最新在前）
    #:   "relevance" 按相关度（仅当用 text_search 且走 FTS 索引时有效）
    order_by: str = "time"
    #: 时间下界（ISO8601，含）
    since: str | None = None
    #: 时间上界（ISO8601，含）
    until: str | None = None
    #: 分页大小
    limit: int = DEFAULT_PAGE_SIZE
    #: 游标：上一页最后一条的 (created_at, id)。用 ``cursor_from`` 生成。
    cursor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "voice_id": self.voice_id,
            "character_id": self.character_id,
            "emotion": self.emotion,
            "only_failed": self.only_failed,
            "only_success": self.only_success,
            "text_contains": self.text_contains,
            "text_search": self.text_search,
            "order_by": self.order_by,
            "since": self.since,
            "until": self.until,
            "limit": self.limit,
            "cursor": self.cursor,
        }


def encode_cursor(created_at: str, row_id: int) -> str:
    """把 (created_at, id) 编码成不透明游标。

    用 base64 而非直接拼接，是为了让调用方不去依赖内部格式。
    """
    import base64

    raw = json.dumps({"t": created_at, "i": row_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[str, int]:
    """解码游标。格式非法时抛 ValueError。"""
    import base64

    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        obj = json.loads(raw)
        return str(obj["t"]), int(obj["i"])
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"游标格式非法：{cursor!r}") from exc


@dataclass(slots=True)
class Page:
    """一页查询结果。"""

    items: list[SynthesisRecord] = field(default_factory=list)
    #: 下一页游标；None 表示没有更多
    next_cursor: str | None = None
    #: 是否还有更多
    has_more: bool = False
    #: 本次返回条数
    count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [i.to_dict() for i in self.items],
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
            "count": self.count,
        }


@dataclass(slots=True)
class Stats:
    """合成记录统计。"""

    total: int = 0
    success: int = 0
    failed: int = 0
    #: 音频总时长（秒）
    total_audio_s: float = 0.0
    #: 合成总耗时（秒）
    total_synth_s: float = 0.0
    #: RTF 中位数
    rtf_median: float = 0.0
    #: RTF 均值
    rtf_mean: float = 0.0
    #: RTF 最小/最大
    rtf_min: float = 0.0
    rtf_max: float = 0.0
    #: 按音色分组计数
    by_voice: dict[str, int] = field(default_factory=dict)
    #: 按错误码分组计数
    by_error: dict[str, int] = field(default_factory=dict)
    #: 时间范围
    first_at: str | None = None
    last_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "success": self.success,
            "failed": self.failed,
            "success_rate": round(self.success / self.total, 4) if self.total else 0.0,
            "total_audio_s": round(self.total_audio_s, 2),
            "total_synth_s": round(self.total_synth_s, 2),
            "rtf_median": round(self.rtf_median, 4),
            "rtf_mean": round(self.rtf_mean, 4),
            "rtf_min": round(self.rtf_min, 4),
            "rtf_max": round(self.rtf_max, 4),
            "by_voice": self.by_voice,
            "by_error": self.by_error,
            "first_at": self.first_at,
            "last_at": self.last_at,
        }


class SynthesisHistoryRepository:
    """合成记录仓储。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def query(self, f: QueryFilter) -> Page:
        """按条件分页查询。

        排序有两种：
          - ``order_by="time"``（默认）：按时间倒序，用 ``(created_at, id)``
            游标分页。记录持续写入时不会重复或跳过。
          - ``order_by="relevance"``：按 FTS5 的 ``bm25`` 相关度排序。
            **此时不能用时间游标**（相关度与时间无关），改用 offset 分页。
            offset 在数据增长时可能重复，但相关度排序本身就是「一次搜索」
            场景，翻页深度有限，可接受。

        相关度排序只在「用 text_search 且查询长度够走 FTS」时生效，
        否则自动回退到时间排序（LIKE 没有相关度概念）。
        """
        limit = max(1, min(f.limit, MAX_PAGE_SIZE))
        use_relevance = (
            f.order_by == "relevance"
            and bool(f.text_search)
            and len((f.text_search or "").strip()) >= FTS_MIN_QUERY_LEN
        )

        where, params = self._build_where(f)

        # 游标条件（仅时间排序）：取「比游标更旧」的记录
        if f.cursor and not use_relevance:
            try:
                c_time, c_id = decode_cursor(f.cursor)
            except ValueError:
                raise
            where.append("(created_at < ? OR (created_at = ? AND id < ?))")
            params.extend([c_time, c_time, c_id])

        sql = (
            "SELECT id, request_id, text, character_id, speaker_id, emotion, style,"
            " speed, instruction, sample_rate, duration_s, total_ms, first_audio_ms,"
            " rtf, peak_vram_mb, engine, error_code, created_at"
            " FROM synthesis_logs"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)

        if use_relevance:
            # FTS5 的 bm25() 越小越相关，升序即「最相关在前」。
            #
            # 关键：bm25() 必须在**直接查 FTS 表的那个 SELECT 里**调用，
            # 不能在 JOIN 的外层用 `bm25(synthesis_fts)` —— 那时
            # synthesis_fts 不是可见的表名，会报 "no such column"。
            # 正确写法是在子查询里算 rank 并带出来。
            sql = (
                "SELECT s.id, s.request_id, s.text, s.character_id, s.speaker_id,"
                " s.emotion, s.style, s.speed, s.instruction, s.sample_rate,"
                " s.duration_s, s.total_ms, s.first_audio_ms, s.rtf,"
                " s.peak_vram_mb, s.engine, s.error_code, s.created_at"
                " FROM synthesis_logs s"
                " JOIN ("
                "   SELECT rowid AS rid, bm25(synthesis_fts) AS rank"
                "   FROM synthesis_fts WHERE synthesis_fts MATCH ?"
                " ) fts ON s.id = fts.rid"
            )
            fts_param = _fts_query((f.text_search or "").strip())
            rest_where = where
            rest_params = params
            if rest_where:
                sql += " WHERE " + " AND ".join(rest_where)
            sql += " ORDER BY fts.rank ASC, s.created_at DESC LIMIT ? OFFSET ?"
            params = [fts_param, *rest_params, limit + 1, 0]
        else:
            sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
            params.append(limit + 1)

        conn = self._db.connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # FTS 查询语法/索引异常时降级到 LIKE，保证功能不整体失败。
            if not f.text_search:
                raise
            fallback = QueryFilter(
                voice_id=f.voice_id,
                character_id=f.character_id,
                emotion=f.emotion,
                only_failed=f.only_failed,
                only_success=f.only_success,
                text_contains=f.text_search,
                text_search=None,
                order_by="time",
                since=f.since,
                until=f.until,
                limit=f.limit,
                cursor=None,
            )
            return self.query(fallback)

        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [SynthesisRecord.from_row(r) for r in rows]

        # 相关度排序下没有游标（顺序与时间无关），用 has_more 提示还有更多
        next_cursor = None
        if has_more and items and not use_relevance:
            last = items[-1]
            next_cursor = encode_cursor(last.created_at, last.id)

        return Page(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
            count=len(items),
        )

    def get(self, record_id: int) -> SynthesisRecord | None:
        """按主键取单条。"""
        conn = self._db.connect()
        row = conn.execute(
            "SELECT id, request_id, text, character_id, speaker_id, emotion, style,"
            " speed, instruction, sample_rate, duration_s, total_ms, first_audio_ms,"
            " rtf, peak_vram_mb, engine, error_code, created_at"
            " FROM synthesis_logs WHERE id = ?",
            (record_id,),
        ).fetchone()
        return SynthesisRecord.from_row(row) if row else None

    def get_by_request_id(self, request_id: str) -> list[SynthesisRecord]:
        """按 request_id 取记录（一个请求可能有多条，如重试）。"""
        conn = self._db.connect()
        rows = conn.execute(
            "SELECT id, request_id, text, character_id, speaker_id, emotion, style,"
            " speed, instruction, sample_rate, duration_s, total_ms, first_audio_ms,"
            " rtf, peak_vram_mb, engine, error_code, created_at"
            " FROM synthesis_logs WHERE request_id = ?"
            " ORDER BY created_at DESC, id DESC",
            (request_id,),
        ).fetchall()
        return [SynthesisRecord.from_row(r) for r in rows]

    def stats(self, f: QueryFilter | None = None) -> Stats:
        """统计。可选按同样条件过滤，但忽略分页。"""
        f = f or QueryFilter()
        where, params = self._build_where(f)

        base = "FROM synthesis_logs"
        if where:
            base += " WHERE " + " AND ".join(where)

        # 只统计成功记录的 RTF 时，需要在其后追加条件。
        # 若 base 里已有 WHERE 用 AND，否则用 WHERE —— 直接拼 " AND " 会在
        # 无筛选条件时产生 `FROM synthesis_logs AND error_code = ''` 语法错误。
        ok_base = base + (" AND " if where else " WHERE ") + "error_code = ''"

        conn = self._db.connect()
        row = conn.execute(
            "SELECT COUNT(*) AS n,"
            " SUM(CASE WHEN error_code = '' THEN 1 ELSE 0 END) AS ok,"
            " SUM(CASE WHEN error_code != '' THEN 1 ELSE 0 END) AS bad,"
            " SUM(duration_s) AS dur,"
            " SUM(total_ms) AS ms,"
            " MIN(rtf) AS rtf_min,"
            " MAX(rtf) AS rtf_max,"
            " MIN(created_at) AS first_at,"
            " MAX(created_at) AS last_at"
            f" {base}",
            params,
        ).fetchone()

        total = int(row["n"] or 0)
        s = Stats(
            total=total,
            success=int(row["ok"] or 0),
            failed=int(row["bad"] or 0),
            total_audio_s=float(row["dur"] or 0.0),
            total_synth_s=float(row["ms"] or 0.0) / 1000.0,
            rtf_min=float(row["rtf_min"] or 0.0),
            rtf_max=float(row["rtf_max"] or 0.0),
            first_at=row["first_at"],
            last_at=row["last_at"],
        )

        # RTF 中位数：SQLite 没有 median 聚合，用窗口函数取中间位置。
        # 注意：必须用「成功记录数」而非总记录数来定位中间行，
        # 因为失败记录的 rtf 为 0，混进来会把中位数拉低。
        if total:
            ok_count = s.success
            if ok_count:
                # 奇数取中间 1 行，偶数取中间 2 行求平均
                if ok_count % 2 == 1:
                    positions = [(ok_count + 1) // 2]
                else:
                    positions = [ok_count // 2, ok_count // 2 + 1]

                placeholders = ",".join("?" * len(positions))
                mrow = conn.execute(
                    "SELECT rtf FROM ("
                    "  SELECT rtf, ROW_NUMBER() OVER (ORDER BY rtf) AS rn"
                    f"  {ok_base}"
                    f") WHERE rn IN ({placeholders})",
                    [*params, *positions],
                ).fetchall()
                vals = [float(r["rtf"]) for r in mrow]
                if vals:
                    s.rtf_median = sum(vals) / len(vals)

            avg = conn.execute(f"SELECT AVG(rtf) AS a {ok_base}", params).fetchone()
            s.rtf_mean = float(avg["a"] or 0.0) if avg else 0.0

        # 分组统计
        for r in conn.execute(
            "SELECT COALESCE(NULLIF(speaker_id, ''), '(未知)') AS k, COUNT(*) AS n"
            f" {base} GROUP BY k ORDER BY n DESC",
            params,
        ).fetchall():
            s.by_voice[str(r["k"])] = int(r["n"])

        err_base = base + (" AND " if where else " WHERE ") + "error_code != ''"
        for r in conn.execute(
            f"SELECT error_code AS k, COUNT(*) AS n {err_base}"
            " GROUP BY k ORDER BY n DESC",
            params,
        ).fetchall():
            s.by_error[str(r["k"])] = int(r["n"])

        return s

    # ------------------------------------------------------------------
    # 维护
    # ------------------------------------------------------------------

    def delete_before(
        self, cutoff_iso: str, *, triggered_by: str = "manual"
    ) -> int:
        """删除指定时间之前的记录，返回删除条数。

        用于定期清理，避免表无限增长。
        每次清理都写入 ``cleanup_logs`` 审计表（谁在什么时候删了多少）。

        :param triggered_by: manual / scheduled，用于事后区分人工与自动清理
        """
        conn = self._db.connect()
        with self._db.transaction() as tx:
            cur = tx.execute(
                "DELETE FROM synthesis_logs WHERE created_at < ?", (cutoff_iso,)
            )
            removed = cur.rowcount or 0
            tx.execute(
                "INSERT INTO cleanup_logs (kind, cutoff, removed, triggered_by,"
                " detail, created_at) VALUES (?,?,?,?,?,?)",
                ("synthesis", cutoff_iso, removed, triggered_by, "", _now()),
            )
            return removed

    def record_cleanup(
        self,
        *,
        kind: str,
        cutoff: str,
        removed: int,
        triggered_by: str = "manual",
        detail: dict[str, Any] | None = None,
    ) -> None:
        """写入一条清理审计记录。

        为什么单独提供：日志文件清理不在 ``delete_before`` 里
        （那是数据库操作），但同样需要审计。
        """
        with self._db.transaction() as tx:
            tx.execute(
                "INSERT INTO cleanup_logs (kind, cutoff, removed, triggered_by,"
                " detail, created_at) VALUES (?,?,?,?,?,?)",
                (
                    kind,
                    cutoff,
                    removed,
                    triggered_by,
                    json.dumps(detail, ensure_ascii=False) if detail else "",
                    _now(),
                ),
            )

    def cleanup_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """读取清理审计记录（最新在前）。"""
        conn = self._db.connect()
        rows = conn.execute(
            "SELECT kind, cutoff, removed, triggered_by, detail, created_at"
            " FROM cleanup_logs ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        ).fetchall()

        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            raw = d.get("detail") or ""
            try:
                d["detail"] = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                d["detail"] = {"raw": raw}
            out.append(d)
        return out

    def iter_export(
        self,
        f: QueryFilter,
        *,
        fmt: str = "csv",
        max_rows: int = EXPORT_MAX_ROWS,
        page_size: int = MAX_PAGE_SIZE,
    ):  # noqa: ANN201
        """流式导出：逐页产出文本块。

        与 ``export()`` 的区别：
          - ``export()`` 一次性返回全部字节，适合小数据
          - ``iter_export()`` 边查边产出，适合大数据（内存不随行数增长）

        用途：HTTP 层用 StreamingResponse 包起来，浏览器能边收边写盘，
        用户不会看到"卡住不动"。同时前端可以读 ``x-export-total`` 头
        显示总行数。

        :param page_size: 内部每次查多少行（影响首个块的延迟）
        """
        import csv
        import io

        base = QueryFilter(
            voice_id=f.voice_id,
            character_id=f.character_id,
            emotion=f.emotion,
            only_failed=f.only_failed,
            only_success=f.only_success,
            text_contains=f.text_contains,
            text_search=f.text_search,
            order_by=f.order_by,
            since=f.since,
            until=f.until,
            limit=min(page_size, MAX_PAGE_SIZE),
            cursor=None,
        )

        if fmt == "json":
            # JSON 流式：先输出头部，再逐条输出，最后收尾。
            # 注意分隔符处理 —— 第一条前不加逗号。
            yield '{\n  "exported_at": ' + json.dumps(_now())
            yield ',\n  "filter": ' + json.dumps(f.to_dict(), ensure_ascii=False)
            yield ',\n  "items": [\n'

            emitted = 0
            cursor: str | None = None
            while emitted < max_rows:
                base.cursor = cursor
                page = self.query(base)
                for r in page.items:
                    if emitted >= max_rows:
                        break
                    if emitted:
                        yield ",\n"
                    yield "    " + json.dumps(
                        r.to_dict(), ensure_ascii=False, default=str
                    )
                    emitted += 1
                if not page.has_more or not page.next_cursor:
                    break
                cursor = page.next_cursor

            yield f"\n  ],\n  \"count\": {emitted}\n}}\n"
            return

        # CSV 流式：先 BOM + 表头，再逐行
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "id", "request_id", "voice_id", "text", "emotion", "style",
            "speed", "instruction", "sample_rate", "duration_s", "total_ms",
            "first_audio_ms", "rtf", "peak_vram_mb", "engine", "error_code",
            "created_at",
        ])
        yield "\ufeff" + buf.getvalue()

        emitted = 0
        cursor = None
        while emitted < max_rows:
            base.cursor = cursor
            page = self.query(base)
            if not page.items:
                break

            buf = io.StringIO()
            writer = csv.writer(buf)
            for r in page.items:
                if emitted >= max_rows:
                    break
                writer.writerow([
                    _csv_safe(v) for v in (
                        r.id, r.request_id, r.voice_id, r.text, r.emotion, r.style,
                        r.speed, r.instruction, r.sample_rate, r.duration_s,
                        r.total_ms, r.first_audio_ms, r.rtf, r.peak_vram_mb,
                        r.engine, r.error_code, r.created_at,
                    )
                ])
                emitted += 1
            yield buf.getvalue()

            if not page.has_more or not page.next_cursor:
                break
            cursor = page.next_cursor

    def export(
        self, f: QueryFilter, *, fmt: str = "csv", max_rows: int = EXPORT_MAX_ROWS
    ) -> tuple[bytes, str, str]:
        """导出查询结果为 CSV 或 JSON（一次性返回全部字节）。

        为什么不用分页接口逐页拉：调用方（前端/脚本）不该自己拼装导出文件，
        而且导出常常需要「当前筛选条件下的全部数据」。

        大数据建议用 ``iter_export()``（流式），避免内存峰值。

        :return: (内容字节, MIME 类型, 建议文件名)
        """
        chunks = list(self.iter_export(f, fmt=fmt, max_rows=max_rows))
        body = "".join(chunks).encode("utf-8")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        if fmt == "json":
            return body, "application/json", f"synthesis-history-{stamp}.json"
        return body, "text/csv; charset=utf-8", f"synthesis-history-{stamp}.csv"

    def count_for_export(self, f: QueryFilter) -> int:
        """导出前的总数估算（用于 x-export-total 头）。

        复用 stats 的 COUNT 查询，但忽略分页。
        """
        return self.stats(f).total

    def count(self) -> int:
        conn = self._db.connect()
        row = conn.execute("SELECT COUNT(*) AS n FROM synthesis_logs").fetchone()
        return int(row["n"] or 0)

    def rebuild_fts(self) -> int:
        """重建全文索引。

        用于两种情况：
          1. 从 v2 升级到 v3 时，已有记录不在 FTS 表里
          2. 索引损坏

        :return: 重建后的索引行数
        """
        conn = self._db.connect()
        with self._db.transaction() as tx:
            tx.execute("INSERT INTO synthesis_fts(synthesis_fts) VALUES ('rebuild')")
        row = conn.execute("SELECT COUNT(*) AS n FROM synthesis_fts").fetchone()
        return int(row["n"] or 0)

    # ------------------------------------------------------------------

    def _build_where(self, f: QueryFilter) -> tuple[list[str], list[Any]]:
        """构造 WHERE 子句。全部参数化，不拼接用户输入。

        全文搜索（text_search）用 FTS5 子查询，走倒排索引；
        模糊匹配（text_contains）用 LIKE，无索引但支持任意子串。

        **自动降级**：FTS5 的 trigram 分词器要求查询串 >= 3 字符
        （实测 2 字返回 0 条）。短查询自动改用 LIKE，保证结果正确。
        调用方不需要关心这个细节。
        """
        where: list[str] = []
        params: list[Any] = []

        if f.voice_id:
            # 合成记录用 speaker_id 存音色标识
            where.append("speaker_id = ?")
            params.append(f.voice_id)
        if f.character_id:
            where.append("character_id = ?")
            params.append(f.character_id)
        if f.emotion:
            where.append("emotion = ?")
            params.append(f.emotion)
        if f.only_failed:
            where.append("error_code != ''")
        if f.only_success:
            where.append("error_code = ''")

        # 全文搜索：够长才走 FTS，否则降级 LIKE
        if f.text_search:
            term = f.text_search.strip()
            if len(term) >= FTS_MIN_QUERY_LEN:
                where.append(
                    "id IN ("
                    " SELECT rowid FROM synthesis_fts WHERE synthesis_fts MATCH ?"
                    ")"
                )
                params.append(_fts_query(term))
            else:
                # trigram 对 2 字查询无效，用 LIKE 保证正确性
                where.append("(text LIKE ? OR instruction LIKE ?)")
                params.extend([f"%{term}%", f"%{term}%"])
        elif f.text_contains:
            where.append("text LIKE ?")
            params.append(f"%{f.text_contains}%")

        if f.since:
            where.append("created_at >= ?")
            params.append(f.since)
        if f.until:
            where.append("created_at <= ?")
            params.append(f.until)

        return where, params


def _fts_query(raw: str) -> str:
    """把用户输入转成 FTS5 查询串。

    为什么要处理：FTS5 有特殊语法（``*`` ``"`` ``-`` ``AND`` ``NEAR`` 等），
    直接传入任意文本会抛 ``sqlite3.OperationalError: fts5: syntax error``。
    例如用户搜 ``C++`` 或 ``"未闭合引号`` 都会炸。

    做法：按空白切词，每个词用双引号包起来（引号内的特殊字符失去语义），
    词之间默认 AND。这样既能搜中文整句（unicode61 按字切分），
    也不会因特殊字符报错。
    """
    tokens = [t for t in raw.split() if t]
    if not tokens:
        return '""'
    # 转义内部双引号（FTS5 用 "" 表示一个字面引号）
    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    return " ".join(quoted)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "EXPORT_MAX_ROWS",
    "MAX_PAGE_SIZE",
    "Page",
    "QueryFilter",
    "Stats",
    "SynthesisHistoryRepository",
    "SynthesisRecord",
    "decode_cursor",
    "encode_cursor",
]
