"""日志读取：把 JSON Lines 日志文件解析成可查询的记录。

为什么需要单独一层：
  - 日志是纯文本文件，直接暴露给前端等于让浏览器读服务器文件（不可行）
  - 需要支持按级别/request_id/关键词过滤、限制条数、反向读取（最新在前）
  - 文件可能很大（长期运行几十 MB），不能一次全读进内存

设计要点：
  - **反向读取**：从文件尾部按块读取，只取需要的行数。
    这样「看最近 100 条」不会因为文件有 50MB 而变慢。
  - **容忍坏行**：日志可能被截断（进程被杀），解析失败的行跳过并计数，
    不能因为一行坏了就整体失败。
  - **不返回敏感字段**：日志写入时已脱敏（见 core/logging.py 的 _redact），
    这里再兜底过滤一遍 key 名。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ..core.paths import log_dir

#: 日志文件名
LOG_FILENAME = "zjy14.log"

#: 单次最多返回条数
MAX_LOG_LINES = 1000

#: 默认返回条数
DEFAULT_LOG_LINES = 100

#: 反向读取时每次从文件读取的字节数
_CHUNK_SIZE = 64 * 1024

#: 二次兜底脱敏的 key（小写匹配）
_SENSITIVE_KEYS = frozenset(
    {"api_key", "auth_token", "token", "password", "secret", "authorization"}
)


@dataclass(slots=True)
class LogEntry:
    """一条日志。"""

    ts: str = ""
    level: str = ""
    logger: str = ""
    event: str = ""
    request_id: str = ""
    #: 其余业务字段
    fields: dict[str, Any] = field(default_factory=dict)
    #: 异常堆栈（若有）
    exc: str = ""
    #: 行号（从 1 开始，便于定位）
    line_no: int = 0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ts": self.ts,
            "level": self.level,
            "logger": self.logger,
            "event": self.event,
            "line_no": self.line_no,
        }
        if self.request_id:
            d["request_id"] = self.request_id
        if self.exc:
            d["exc"] = self.exc
        if self.fields:
            d["fields"] = self.fields
        return d


@dataclass(slots=True)
class LogQuery:
    """日志查询条件。"""

    #: 返回条数上限
    limit: int = DEFAULT_LOG_LINES
    #: 级别过滤（可多个），空表示全部
    levels: list[str] = field(default_factory=list)
    #: 按 request_id 精确过滤
    request_id: str | None = None
    #: 事件/字段关键词（子串匹配，不区分大小写）
    keyword: str | None = None
    #: logger 名过滤（如 routes.synthesis）
    logger_name: str | None = None
    #: 是否只返回带异常的
    only_errors: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "levels": self.levels,
            "request_id": self.request_id,
            "keyword": self.keyword,
            "logger_name": self.logger_name,
            "only_errors": self.only_errors,
        }


@dataclass(slots=True)
class LogResult:
    """日志查询结果。"""

    entries: list[LogEntry] = field(default_factory=list)
    #: 扫描过的行数（用于判断是否因 limit 截断）
    scanned: int = 0
    #: 解析失败的行数（坏行）
    bad_lines: int = 0
    #: 是否还有更早的日志未返回
    has_more: bool = False
    #: 日志文件路径
    path: str = ""
    #: 文件大小（字节）
    file_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "count": len(self.entries),
            "scanned": self.scanned,
            "bad_lines": self.bad_lines,
            "has_more": self.has_more,
            "path": self.path,
            "file_size": self.file_size,
        }


def log_file_path() -> Path:
    """当前日志文件路径。"""
    return log_dir() / LOG_FILENAME


def _redact(obj: Any) -> Any:
    """递归脱敏（兜底，正常写入时已脱敏）。"""
    if isinstance(obj, dict):
        return {
            k: ("<set>" if v else "<empty>") if k.lower() in _SENSITIVE_KEYS
            else _redact(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _parse_line(line: str, line_no: int) -> LogEntry | None:
    """解析一行 JSON。失败返回 None（坏行）。"""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    known = {"ts", "level", "logger", "event", "request_id", "exc"}
    # 注意：必须对每个顶层字段单独判断敏感 key。
    # 早期版本用 {k: _redact(v) ...} 只对「值」递归，导致
    # 顶层的 api_key / auth_token 原样返回（_redact 只处理 dict 内部）。
    fields: dict[str, Any] = {}
    for k, v in obj.items():
        if k in known:
            continue
        if k.lower() in _SENSITIVE_KEYS:
            fields[k] = "<set>" if v else "<empty>"
        else:
            fields[k] = _redact(v)

    return LogEntry(
        ts=str(obj.get("ts", "")),
        level=str(obj.get("level", "")),
        logger=str(obj.get("logger", "")),
        event=str(obj.get("event", "")),
        request_id=str(obj.get("request_id", "") or ""),
        fields=fields,
        exc=str(obj.get("exc", "") or ""),
        line_no=line_no,
    )


def _iter_tail_lines(path: Path, max_lines: int) -> Iterator[tuple[int, str]]:
    """从文件尾部反向读取，最多 max_lines 行。

    为什么反向读：日志持续追加，「看最近 N 条」是主要场景。
    正向读需要扫完整个文件，文件大了会很慢。

    :return: (行号, 行内容) 的迭代器，顺序为从新到旧
    """
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size == 0:
            return

        # 先统计总行数，才能给出行号。
        # 大文件下这是唯一一次全文件扫描，但只读不解析，成本可接受。
        total_lines = 0
        f.seek(0)
        while True:
            block = f.read(_CHUNK_SIZE)
            if not block:
                break
            total_lines += block.count(b"\n")
        f.seek(-1, 2)
        ends_with_newline = f.read(1) == b"\n"
        if not ends_with_newline:
            total_lines += 1  # 末行没有换行符，也是一行

        pos = size
        buf = b""
        yielded = 0
        # 下一条要产出的行号（从总行数递减）
        next_line_no = total_lines

        while pos > 0 and yielded < max_lines:
            read_size = min(_CHUNK_SIZE, pos)
            pos -= read_size
            f.seek(pos)
            buf = f.read(read_size) + buf

            parts = buf.split(b"\n")
            if pos > 0:
                # 第一个 part 可能是半行（跨块），留给下一轮
                buf = parts[0]
                complete = parts[1:]
            else:
                buf = b""
                complete = parts

            # 文件以换行结尾时，split 会在末尾产生一个空元素 ——
            # 它不是真实的行，必须丢弃，否则行号会整体偏移一位。
            # 只在处理尾部第一块时需要，之后各块后面还有内容，不会有此产物。
            if ends_with_newline and complete and complete[-1] == b"":
                complete = complete[:-1]
                ends_with_newline = False

            for raw in reversed(complete):
                if yielded >= max_lines:
                    break
                yield next_line_no, raw.decode("utf-8", errors="replace")
                yielded += 1
                next_line_no -= 1


def query_logs(q: LogQuery) -> LogResult:
    """查询日志。最新在前。"""
    path = log_file_path()
    result = LogResult(path=str(path))

    if not path.is_file():
        return result

    result.file_size = path.stat().st_size
    limit = max(1, min(q.limit, MAX_LOG_LINES))

    levels = {lv.upper() for lv in q.levels if lv}
    keyword = (q.keyword or "").lower()

    # 多读一些，因为过滤会丢弃一部分
    scan_budget = min(limit * 20 + 200, 20000)

    for line_no, line in _iter_tail_lines(path, scan_budget):
        result.scanned += 1
        entry = _parse_line(line, line_no)
        if entry is None:
            result.bad_lines += 1
            continue

        if not _match(entry, levels, q, keyword):
            continue

        result.entries.append(entry)
        if len(result.entries) >= limit:
            result.has_more = True
            break

    return result


def _match(
    e: LogEntry,
    levels: set[str],
    q: LogQuery,
    keyword: str,
) -> bool:
    """判断一条日志是否满足条件。"""
    if levels and e.level.upper() not in levels:
        return False
    if q.only_errors and not e.exc and e.level.upper() not in ("ERROR", "CRITICAL"):
        return False
    if q.request_id and e.request_id != q.request_id:
        return False
    if q.logger_name and q.logger_name not in e.logger:
        return False
    if keyword:
        haystack = f"{e.event} {e.logger} {e.request_id} {json.dumps(e.fields, ensure_ascii=False)}"
        if keyword not in haystack.lower():
            return False
    return True


def available_log_files() -> list[dict[str, Any]]:
    """列出日志目录下所有文件（含轮转归档）。

    日志用 TimedRotatingFileHandler 按天轮转，会生成
    ``zjy14.log.2026-11-15`` 这类文件。
    """
    d = log_dir()
    if not d.is_dir():
        return []

    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("zjy14.log*"), reverse=True):
        if not p.is_file():
            continue
        st = p.stat()
        out.append({
            "name": p.name,
            "size": st.st_size,
            "modified_at": __import__("datetime").datetime.fromtimestamp(
                st.st_mtime
            ).astimezone().isoformat(timespec="seconds"),
            "is_current": p.name == LOG_FILENAME,
        })
    return out


__all__ = [
    "DEFAULT_LOG_LINES",
    "LOG_FILENAME",
    "MAX_LOG_LINES",
    "LogEntry",
    "LogQuery",
    "LogResult",
    "available_log_files",
    "log_file_path",
    "query_logs",
]
