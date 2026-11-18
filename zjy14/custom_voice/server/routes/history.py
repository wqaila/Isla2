"""合成记录与日志查询 API。

端点：
    GET  /api/v1/history                     合成记录分页查询
    GET  /api/v1/history/stats               合成记录统计
    GET  /api/v1/history/export              导出 CSV / JSON
    GET  /api/v1/history/cleanup-history     清理审计记录
    GET  /api/v1/history/{record_id}         单条记录详情
    GET  /api/v1/history/by-request/{rid}    按 request_id 查（一个请求可能多条）
    DELETE /api/v1/history                   按时间清理旧记录
    GET  /api/v1/logs                        日志查询（JSON Lines 解析）
    GET  /api/v1/logs/files                  日志文件列表（含轮转归档）

设计说明：
  - 路由层只做参数校验与响应组装，查询逻辑在 ``history/`` 包
  - 分页用游标，不用 OFFSET（记录持续写入，OFFSET 会重复/跳过）
  - 日志反向读取，只取需要的条数，大文件不卡
  - 敏感字段在写入时已脱敏，读取时再兜底过滤一遍
  - 全文搜索走 FTS5 索引；FTS 不可用时自动降级到 LIKE
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ...core.errors import InvalidRequestError, NotFoundError
from ...core.logging import get_logger
from ...history import logs as log_reader
from ...history.repository import (
    EXPORT_MAX_ROWS,
    MAX_PAGE_SIZE,
    QueryFilter,
    SynthesisHistoryRepository,
)
from ...schemas.history import (
    CleanupRecordOut,
    HistoryPageOut,
    HistoryQuery,
    HistoryStatsOut,
    LogFileOut,
    LogPageOut,
    SynthesisRecordOut,
)
from ..deps import get_state

router = APIRouter(prefix="/api/v1", tags=["history"])
logger = get_logger("routes.history")


def _repo(request: Request) -> SynthesisHistoryRepository:
    return SynthesisHistoryRepository(get_state(request).db)


def _to_filter(q: HistoryQuery) -> QueryFilter:
    return QueryFilter(
        voice_id=q.voice_id,
        character_id=q.character_id,
        emotion=q.emotion,
        only_failed=q.only_failed,
        only_success=q.only_success,
        text_contains=q.text_contains,
        text_search=q.text_search,
        since=q.since,
        until=q.until,
        limit=q.limit,
        cursor=q.cursor,
    )


# ----------------------------------------------------------------------
# 合成记录
# ----------------------------------------------------------------------


@router.get("/history", response_model=HistoryPageOut)
async def list_history(
    request: Request,
    voice_id: str | None = Query(default=None, description="按音色筛选"),
    character_id: str | None = Query(default=None, description="按角色筛选"),
    emotion: str | None = Query(default=None, description="按情绪标签筛选"),
    only_failed: bool = Query(default=False, description="只看失败的"),
    only_success: bool = Query(default=False, description="只看成功的"),
    text_contains: str | None = Query(
        default=None, description="文本模糊匹配（LIKE，无索引）"
    ),
    text_search: str | None = Query(
        default=None, description="全文搜索（FTS5 索引，比 LIKE 快）"
    ),
    since: str | None = Query(default=None, description="时间下界（ISO8601）"),
    until: str | None = Query(default=None, description="时间上界（ISO8601）"),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE, description="每页条数"),
    cursor: str | None = Query(default=None, description="上一页的 next_cursor"),
) -> HistoryPageOut:
    """分页查询合成记录，按时间倒序（最新在前）。

    分页用游标：把上一页返回的 ``next_cursor`` 原样传回来即可。

    ``text_search`` 走全文索引（FTS5），``text_contains`` 走 LIKE。
    前者快，后者支持任意子串；同时给时以 ``text_search`` 为准。
    """
    if only_failed and only_success:
        raise InvalidRequestError(
            "only_failed 与 only_success 不能同时为 true",
            detail={"only_failed": True, "only_success": True},
        )

    q = HistoryQuery(
        voice_id=voice_id,
        character_id=character_id,
        emotion=emotion,
        only_failed=only_failed,
        only_success=only_success,
        text_contains=text_contains,
        text_search=text_search,
        since=since,
        until=until,
        limit=limit,
        cursor=cursor,
    )

    try:
        page = _repo(request).query(_to_filter(q))
    except ValueError as exc:
        # 游标格式非法 -> 400，而不是 500
        raise InvalidRequestError(str(exc)) from exc

    return HistoryPageOut(
        items=[SynthesisRecordOut(**i.to_dict()) for i in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
        count=page.count,
    )


@router.get("/history/stats", response_model=HistoryStatsOut)
async def history_stats(
    request: Request,
    voice_id: str | None = Query(default=None),
    character_id: str | None = Query(default=None),
    emotion: str | None = Query(default=None),
    only_failed: bool = Query(default=False),
    only_success: bool = Query(default=False),
    text_contains: str | None = Query(default=None),
    text_search: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
) -> HistoryStatsOut:
    """合成记录统计。筛选条件与列表接口一致，但不受分页影响。

    注意路由顺序：``/history/stats`` 必须注册在 ``/history/{record_id}``
    之前，否则会被路径参数吃掉（FastAPI 按注册顺序匹配）。
    """
    q = HistoryQuery(
        voice_id=voice_id,
        character_id=character_id,
        emotion=emotion,
        only_failed=only_failed,
        only_success=only_success,
        text_contains=text_contains,
        text_search=text_search,
        since=since,
        until=until,
    )
    s = _repo(request).stats(_to_filter(q))
    return HistoryStatsOut(**s.to_dict())


@router.get("/history/export")
async def export_history(
    request: Request,
    fmt: str = Query(default="csv", pattern="^(csv|json)$", description="csv 或 json"),
    voice_id: str | None = Query(default=None),
    character_id: str | None = Query(default=None),
    emotion: str | None = Query(default=None),
    only_failed: bool = Query(default=False),
    only_success: bool = Query(default=False),
    text_contains: str | None = Query(default=None),
    text_search: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    max_rows: int = Query(
        default=10000,
        ge=1,
        le=EXPORT_MAX_ROWS,
        description=f"最大导出行数（上限 {EXPORT_MAX_ROWS}）",
    ),
) -> Response:
    """导出当前筛选条件下的合成记录。

    返回文件下载（CSV 带 UTF-8 BOM，Excel 可直接打开中文）。

    注意路由顺序：``/history/export`` 必须在 ``/history/{record_id}`` 之前。
    """
    q = HistoryQuery(
        voice_id=voice_id,
        character_id=character_id,
        emotion=emotion,
        only_failed=only_failed,
        only_success=only_success,
        text_contains=text_contains,
        text_search=text_search,
        since=since,
        until=until,
    )
    body, mime, filename = _repo(request).export(
        _to_filter(q), fmt=fmt, max_rows=max_rows
    )

    logger.info(
        "导出合成记录",
        extra={
            "extra_fields": {
                "format": fmt,
                "bytes": len(body),
                "filename": filename,
            }
        },
    )

    return Response(
        content=body,
        media_type=mime,
        headers={
            "content-disposition": f'attachment; filename="{filename}"',
            "x-export-rows": str(body.count(b"\n")) if fmt == "csv" else "?",
        },
    )


@router.get("/history/cleanup-history", response_model=list[CleanupRecordOut])
async def cleanup_history(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
) -> list[CleanupRecordOut]:
    """清理审计记录（谁在什么时候删了多少）。

    注意路由顺序：必须在 ``/history/{record_id}`` 之前。
    """
    rows = _repo(request).cleanup_history(limit=limit)
    return [CleanupRecordOut(**r) for r in rows]


@router.get("/history/by-request/{request_id}", response_model=list[SynthesisRecordOut])
async def history_by_request(request: Request, request_id: str) -> list[SynthesisRecordOut]:
    """按 request_id 查记录。

    一个 request_id 可能对应多条（重试、流式重发），因此返回列表。
    """
    if not request_id.strip():
        raise InvalidRequestError("request_id 不能为空")
    rows = _repo(request).get_by_request_id(request_id.strip())
    return [SynthesisRecordOut(**r.to_dict()) for r in rows]


@router.get("/history/{record_id}", response_model=SynthesisRecordOut)
async def history_detail(request: Request, record_id: int) -> SynthesisRecordOut:
    """按主键取单条合成记录。"""
    rec = _repo(request).get(record_id)
    if rec is None:
        raise NotFoundError(
            f"合成记录不存在：id={record_id}", detail={"record_id": record_id}
        )
    return SynthesisRecordOut(**rec.to_dict())


@router.delete("/history", status_code=204)
async def purge_history(
    request: Request,
    before: str = Query(..., description="删除此时间之前的记录（ISO8601）"),
) -> Response:
    """清理指定时间之前的合成记录。

    用于定期维护，避免表无限增长。
    返回 204，不返回删除条数（保持 REST 语义；需要条数可查 stats
    或 cleanup-history）。
    """
    if not before.strip():
        raise InvalidRequestError("before 不能为空")
    removed = _repo(request).delete_before(before.strip(), triggered_by="manual")
    logger.info(
        "清理合成记录",
        extra={"extra_fields": {"before": before, "removed": removed}},
    )
    return Response(status_code=204)


# ----------------------------------------------------------------------
# 日志
# ----------------------------------------------------------------------


@router.get("/logs", response_model=LogPageOut)
async def query_logs(
    limit: int = Query(default=100, ge=1, le=log_reader.MAX_LOG_LINES),
    level: list[str] | None = Query(
        default=None,
        description="级别过滤，可重复传。支持模糊：WARN 匹配 WARNING",
    ),
    request_id: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    logger_name: str | None = Query(default=None),
    only_errors: bool = Query(default=False),
    file_name: str | None = Query(
        default=None,
        description="指定日志文件（含归档，如 zjy14.log.2026-11-16）。默认当前日志",
    ),
) -> LogPageOut:
    """查询服务日志（最新在前）。

    日志是 JSON Lines，本接口解析后返回结构化数据，
    浏览器无需直接读文件。

    注意：``level`` 可重复传多个值，如 ``?level=WARNING&level=ERROR``。
    级别支持前缀匹配（``WARN`` -> ``WARNING``）。

    ``file_name`` 可指定归档文件，取值来自 ``/logs/files``。
    """
    q = log_reader.LogQuery(
        limit=limit,
        levels=[lv for lv in (level or []) if lv],
        request_id=request_id,
        keyword=keyword,
        logger_name=logger_name,
        only_errors=only_errors,
        file_name=file_name,
    )
    try:
        result = log_reader.query_logs(q)
    except ValueError as exc:
        # 非法文件名（路径穿越等）-> 400
        raise InvalidRequestError(str(exc)) from exc

    return LogPageOut(**result.to_dict())


@router.get("/logs/files", response_model=list[LogFileOut])
async def list_log_files() -> list[LogFileOut]:
    """列出日志文件（含按天轮转的归档）。"""
    return [LogFileOut(**f) for f in log_reader.available_log_files()]


__all__ = ["router"]
