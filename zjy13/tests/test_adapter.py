"""适配器测试：SSE 解析与错误映射。"""

from __future__ import annotations

import json

import pytest

from server.llm.adapter import get_adapter, OpenAICompatAdapter
from server.llm.base import (
    ChatMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMProtocolError,
    LLMRateLimitError,
    LLMServerError,
)


@pytest.fixture()
def adapter() -> OpenAICompatAdapter:
    return OpenAICompatAdapter()


def test_payload_shape(adapter: OpenAICompatAdapter) -> None:
    payload = adapter.build_payload(
        model="m",
        messages=[ChatMessage("system", "s"), ChatMessage("user", "u")],
        temperature=0.5,
        max_tokens=64,
        stream=True,
        stream_include_usage=False,
    )
    assert payload["model"] == "m"
    assert payload["messages"] == [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert payload["stream"] is True
    assert "stream_options" not in payload  # 默认不发送，避免不兼容


def test_payload_with_usage(adapter: OpenAICompatAdapter) -> None:
    payload = adapter.build_payload(
        model="m",
        messages=[ChatMessage("user", "u")],
        temperature=0.5,
        max_tokens=64,
        stream=True,
        stream_include_usage=True,
    )
    assert payload["stream_options"] == {"include_usage": True}


def test_thinking_disabled_in_payload(adapter: OpenAICompatAdapter) -> None:
    payload = adapter.build_payload(
        model="m",
        messages=[ChatMessage("user", "u")],
        temperature=0.5,
        max_tokens=64,
        stream=True,
        stream_include_usage=False,
        thinking="disabled",
    )
    assert payload["thinking"] == {"type": "disabled"}


def test_thinking_default_omits_field(adapter: OpenAICompatAdapter) -> None:
    """默认不发送 thinking，避免非 DeepSeek 服务商因未知字段报错。"""
    payload = adapter.build_payload(
        model="m",
        messages=[ChatMessage("user", "u")],
        temperature=0.5,
        max_tokens=64,
        stream=True,
        stream_include_usage=False,
    )
    assert "thinking" not in payload


def test_reasoning_content_not_treated_as_text(adapter: OpenAICompatAdapter) -> None:
    """思考内容绝不能进入可朗读文本（否则会被 TTS 念出来）。"""
    data = json.dumps(
        {"choices": [{"delta": {"reasoning_content": "内部思考"}, "finish_reason": None}]}
    )
    assert adapter.parse_stream_data(data).kind == "ignore"

    data2 = json.dumps(
        {"choices": [{"delta": {"content": "正文", "reasoning_content": "思考"}, "finish_reason": None}]}
    )
    event = adapter.parse_stream_data(data2)
    assert event.text == "正文"


def test_parse_delta(adapter: OpenAICompatAdapter) -> None:
    data = json.dumps(
        {"choices": [{"delta": {"content": "你好"}, "finish_reason": None}]}, ensure_ascii=False
    )
    event = adapter.parse_stream_data(data)
    assert event.kind == "delta"
    assert event.text == "你好"


def test_parse_finish(adapter: OpenAICompatAdapter) -> None:
    data = json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    event = adapter.parse_stream_data(data)
    assert event.kind == "finish"
    assert event.finish_reason == "stop"


def test_parse_done_marker(adapter: OpenAICompatAdapter) -> None:
    assert adapter.parse_stream_data("[DONE]").finish_reason == "done"


def test_parse_usage_only_chunk(adapter: OpenAICompatAdapter) -> None:
    data = json.dumps({"choices": [], "usage": {"total_tokens": 3}})
    event = adapter.parse_stream_data(data)
    assert event.kind == "usage"
    assert event.usage == {"total_tokens": 3}


def test_parse_empty_is_ignore(adapter: OpenAICompatAdapter) -> None:
    assert adapter.parse_stream_data("").kind == "ignore"
    assert adapter.parse_stream_data("{}").kind == "ignore"


def test_parse_invalid_json(adapter: OpenAICompatAdapter) -> None:
    with pytest.raises(LLMProtocolError):
        adapter.parse_stream_data("{oops")


def test_parse_response_ok(adapter: OpenAICompatAdapter) -> None:
    text, finish, usage = adapter.parse_response(
        {
            "model": "m",
            "choices": [{"message": {"role": "assistant", "content": "回复"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 10},
        }
    )
    assert text == "回复"
    assert finish == "stop"
    assert usage["total_tokens"] == 10


def test_parse_response_missing_choices(adapter: OpenAICompatAdapter) -> None:
    with pytest.raises(LLMProtocolError):
        adapter.parse_response({"choices": []})


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, LLMAuthError),
        (403, LLMAuthError),
        (429, LLMRateLimitError),
        (500, LLMServerError),
        (503, LLMServerError),
        (400, LLMBadRequestError),
        (404, LLMBadRequestError),
    ],
)
def test_status_mapping(adapter: OpenAICompatAdapter, status: int, expected: type[Exception]) -> None:
    body = json.dumps({"error": {"message": "boom", "code": "x"}}).encode()
    with pytest.raises(expected) as exc_info:
        adapter.raise_for_status(status, body, {})
    assert "boom" in str(exc_info.value)


def test_retry_after_parsed(adapter: OpenAICompatAdapter) -> None:
    with pytest.raises(LLMRateLimitError) as exc_info:
        adapter.raise_for_status(429, b"{}", {"retry-after": "1.5"})
    assert exc_info.value.retry_after_s == 1.5


def test_2xx_passes(adapter: OpenAICompatAdapter) -> None:
    adapter.raise_for_status(200, b"", {})  # 不抛异常


def test_unknown_adapter() -> None:
    with pytest.raises(LLMProtocolError):
        get_adapter("no-such-provider")
