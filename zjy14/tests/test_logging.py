"""结构化日志测试：重点验证 request_id 贯穿与敏感字段脱敏。"""

from __future__ import annotations

import json
import logging

from custom_voice.core.logging import (
    JsonFormatter,
    get_request_id,
    reset_request_id,
    set_request_id,
)


def _record(msg: str, **fields) -> logging.LogRecord:
    rec = logging.LogRecord(
        name="zjy14.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    rec.extra_fields = fields
    return rec


def test_basic_json_shape() -> None:
    fmt = JsonFormatter()
    payload = json.loads(fmt.format(_record("hello", chars=3)))
    assert payload["event"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["chars"] == 3
    assert "ts" in payload


def test_request_id_injected() -> None:
    token = set_request_id("abc123")
    try:
        fmt = JsonFormatter()
        payload = json.loads(fmt.format(_record("with rid")))
        assert payload["request_id"] == "abc123"
    finally:
        reset_request_id(token)


def test_request_id_absent_when_unset() -> None:
    # 确保上下文为空
    token = set_request_id("")
    try:
        payload = json.loads(JsonFormatter().format(_record("no rid")))
        assert "request_id" not in payload
    finally:
        reset_request_id(token)


def test_sensitive_fields_redacted() -> None:
    fmt = JsonFormatter()
    payload = json.loads(
        fmt.format(_record("login", api_key="sk-secret-123", auth_token="tok", user="bob"))
    )
    assert payload["api_key"] == "<set>"
    assert payload["auth_token"] == "<set>"
    assert payload["user"] == "bob"
    assert "sk-secret-123" not in json.dumps(payload)


def test_empty_sensitive_field() -> None:
    payload = json.loads(JsonFormatter().format(_record("x", api_key="")))
    assert payload["api_key"] == "<empty>"


def test_nested_redaction() -> None:
    payload = json.loads(
        JsonFormatter().format(_record("x", cfg={"api_key": "sk-1", "port": 50001}))
    )
    assert payload["cfg"]["api_key"] == "<set>"
    assert payload["cfg"]["port"] == 50001


def test_request_id_roundtrip() -> None:
    assert get_request_id() == ""
    token = set_request_id("r1")
    assert get_request_id() == "r1"
    reset_request_id(token)
    assert get_request_id() == ""
