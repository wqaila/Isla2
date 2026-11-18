"""合成记录与日志查询的请求/响应模型。

分页用游标（cursor）而非页码：
    合成记录持续写入，OFFSET 分页在数据增长时会重复或跳过行。
    游标基于 ``(created_at, id)``，对插入不敏感。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..history.logs import DEFAULT_LOG_LINES, MAX_LOG_LINES
from ..history.repository import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
)


class SynthesisRecordOut(BaseModel):
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


class HistoryPageOut(BaseModel):
    """一页合成记录。"""

    items: list[SynthesisRecordOut]
    next_cursor: str | None = Field(
        default=None, description="下一页游标。为 null 表示没有更多。"
    )
    has_more: bool = False
    count: int = 0


class HistoryStatsOut(BaseModel):
    """合成记录统计。"""

    total: int = 0
    success: int = 0
    failed: int = 0
    success_rate: float = 0.0
    total_audio_s: float = 0.0
    total_synth_s: float = 0.0
    rtf_median: float = 0.0
    rtf_mean: float = 0.0
    rtf_min: float = 0.0
    rtf_max: float = 0.0
    by_voice: dict[str, int] = Field(default_factory=dict)
    by_error: dict[str, int] = Field(default_factory=dict)
    first_at: str | None = None
    last_at: str | None = None


class HistoryQuery(BaseModel):
    """合成记录查询参数。"""

    model_config = ConfigDict(extra="forbid")

    voice_id: str | None = Field(default=None, description="按音色筛选。")
    character_id: str | None = Field(default=None, description="按角色筛选。")
    emotion: str | None = Field(
        default=None, description="按情绪标签筛选（仅元数据，见 D1 结论）。"
    )
    only_failed: bool = Field(default=False, description="只看失败的。")
    only_success: bool = Field(default=False, description="只看成功的。")
    text_contains: str | None = Field(
        default=None, max_length=200, description="文本模糊匹配。"
    )
    since: str | None = Field(default=None, description="时间下界（ISO8601，含）。")
    until: str | None = Field(default=None, description="时间上界（ISO8601，含）。")
    limit: int = Field(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"每页条数（1–{MAX_PAGE_SIZE}）。",
    )
    cursor: str | None = Field(default=None, description="上一页返回的 next_cursor。")


class LogEntryOut(BaseModel):
    """一条日志。"""

    ts: str = ""
    level: str = ""
    logger: str = ""
    event: str = ""
    request_id: str | None = None
    line_no: int = 0
    exc: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class LogQueryParams(BaseModel):
    """日志查询参数。"""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(
        default=DEFAULT_LOG_LINES,
        ge=1,
        le=MAX_LOG_LINES,
        description=f"返回条数（1–{MAX_LOG_LINES}）。",
    )
    levels: list[str] = Field(
        default_factory=list,
        description="级别过滤，如 INFO / WARNING / ERROR。空表示全部。",
    )
    request_id: str | None = Field(default=None, description="按 request_id 精确过滤。")
    keyword: str | None = Field(
        default=None, max_length=200, description="关键词（事件/字段子串匹配）。"
    )
    logger_name: str | None = Field(
        default=None, description="logger 名过滤，如 routes.synthesis。"
    )
    only_errors: bool = Field(default=False, description="只看错误（含异常堆栈）。")


class LogPageOut(BaseModel):
    """日志查询结果。"""

    entries: list[LogEntryOut]
    count: int = 0
    scanned: int = Field(default=0, description="扫描过的行数。")
    bad_lines: int = Field(default=0, description="解析失败的坏行数。")
    has_more: bool = Field(default=False, description="是否还有更早的日志。")
    path: str = ""
    file_size: int = 0


class LogFileOut(BaseModel):
    """日志文件信息。"""

    name: str
    size: int
    modified_at: str
    is_current: bool = False


__all__ = [
    "HistoryPageOut",
    "HistoryQuery",
    "HistoryStatsOut",
    "LogEntryOut",
    "LogFileOut",
    "LogPageOut",
    "LogQueryParams",
    "SynthesisRecordOut",
]
