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
from typing import Any

from ..db.session import Database

#: 单页最大条数。防止调用方一次拉全表。
MAX_PAGE_SIZE = 200

#: 默认页大小
DEFAULT_PAGE_SIZE = 50


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
    #: 文本模糊匹配
    text_contains: str | None = None
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
        """按条件分页查询，按时间倒序（最新在前）。"""
        limit = max(1, min(f.limit, MAX_PAGE_SIZE))
        where, params = self._build_where(f)

        # 游标条件：取「比游标更旧」的记录
        if f.cursor:
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
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        # 多取一条用于判断 has_more
        params.append(limit + 1)

        conn = self._db.connect()
        rows = conn.execute(sql, params).fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        items = [SynthesisRecord.from_row(r) for r in rows]

        next_cursor = None
        if has_more and items:
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

    def delete_before(self, cutoff_iso: str) -> int:
        """删除指定时间之前的记录，返回删除条数。

        用于定期清理，避免表无限增长。
        """
        conn = self._db.connect()
        with self._db.transaction() as tx:
            cur = tx.execute(
                "DELETE FROM synthesis_logs WHERE created_at < ?", (cutoff_iso,)
            )
            return cur.rowcount or 0

    def count(self) -> int:
        conn = self._db.connect()
        row = conn.execute("SELECT COUNT(*) AS n FROM synthesis_logs").fetchone()
        return int(row["n"] or 0)

    # ------------------------------------------------------------------

    def _build_where(self, f: QueryFilter) -> tuple[list[str], list[Any]]:
        """构造 WHERE 子句。全部参数化，不拼接用户输入。"""
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
        if f.text_contains:
            where.append("text LIKE ?")
            params.append(f"%{f.text_contains}%")
        if f.since:
            where.append("created_at >= ?")
            params.append(f.since)
        if f.until:
            where.append("created_at <= ?")
            params.append(f.until)

        return where, params


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "Page",
    "QueryFilter",
    "Stats",
    "SynthesisHistoryRepository",
    "SynthesisRecord",
    "decode_cursor",
    "encode_cursor",
]
