"""history 包：合成记录查询、统计与日志读取。"""

from .logs import (
    DEFAULT_LOG_LINES,
    LOG_FILENAME,
    MAX_LOG_LINES,
    LogEntry,
    LogQuery,
    LogResult,
    available_log_files,
    log_file_path,
    query_logs,
)
from .repository import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
    QueryFilter,
    Stats,
    SynthesisHistoryRepository,
    SynthesisRecord,
    decode_cursor,
    encode_cursor,
)

__all__ = [
    "DEFAULT_LOG_LINES",
    "DEFAULT_PAGE_SIZE",
    "LOG_FILENAME",
    "MAX_LOG_LINES",
    "MAX_PAGE_SIZE",
    "LogEntry",
    "LogQuery",
    "LogResult",
    "Page",
    "QueryFilter",
    "Stats",
    "SynthesisHistoryRepository",
    "SynthesisRecord",
    "available_log_files",
    "decode_cursor",
    "encode_cursor",
    "log_file_path",
    "query_logs",
]
