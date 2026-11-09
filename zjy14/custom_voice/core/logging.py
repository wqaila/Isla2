"""结构化日志。

要求（对应原提示词）："记录 request_id、角色、耗时、显存和错误码"。

实现：
- JSON Lines 格式，一行一个事件，便于 grep 和后续采集
- ``request_id`` 用 contextvars 贯穿，无需层层传参
- 敏感字段（api_key / auth_token）只记录是否设置
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from .paths import log_dir

#: 当前请求的 request_id。由中间件设置，日志自动带上。
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)

#: 需要脱敏的字段名（小写匹配）
_SENSITIVE_KEYS = frozenset(
    {"api_key", "auth_token", "token", "password", "secret", "authorization"}
)


def set_request_id(rid: str) -> contextvars.Token[str]:
    """设置当前上下文的 request_id，返回可用于还原的 token。"""
    return _request_id.set(rid)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id.reset(token)


def get_request_id() -> str:
    return _request_id.get()


def _redact(obj: Any) -> Any:
    """递归脱敏。"""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k.lower() in _SENSITIVE_KEYS:
                out[k] = "<set>" if v else "<empty>"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_redact(v) for v in obj]
    return obj


class JsonFormatter(logging.Formatter):
    """把 LogRecord 序列化成单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        rid = get_request_id()
        if rid:
            payload["request_id"] = rid

        # 业务字段通过 extra={"extra_fields": {...}} 传入
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict) and extra:
            payload.update(_redact(extra))

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(
    *,
    level: int = logging.INFO,
    to_file: bool = True,
    to_stderr: bool = True,
) -> logging.Logger:
    """配置根 logger。幂等：重复调用不会叠加 handler。"""
    root = logging.getLogger("zjy14")
    root.setLevel(level)
    root.handlers.clear()
    root.propagate = False

    formatter = JsonFormatter()

    if to_stderr:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(formatter)
        root.addHandler(sh)

    if to_file:
        from logging.handlers import TimedRotatingFileHandler

        log_dir().mkdir(parents=True, exist_ok=True)
        fh = TimedRotatingFileHandler(
            log_dir() / "zjy14.log",
            when="midnight",
            backupCount=7,
            encoding="utf-8",
        )
        fh.setFormatter(formatter)
        root.addHandler(fh)

    return root


def get_logger(name: str) -> logging.Logger:
    """取子 logger。传入不含前缀的短名，如 ``engine``。"""
    return logging.getLogger(f"zjy14.{name}")


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    """记录一个带业务字段的结构化事件。"""
    logger.log(level, event, extra={"extra_fields": fields})


__all__ = [
    "JsonFormatter",
    "get_logger",
    "get_request_id",
    "log_event",
    "reset_request_id",
    "set_request_id",
    "setup_logging",
]
