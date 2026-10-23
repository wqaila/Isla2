"""LLMClient 测试：真实 HTTP 路径（本地假服务端）+ 重试/取消/去重。

说明：这里全部使用本地假服务端或 httpx MockTransport，
因此**不代表任何真实服务商的行为**，只验证客户端代码路径正确。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from server.config import LLMConfig
from server.llm import (
    ChatMessage,
    LLMAuthError,
    LLMCancelledError,
    LLMDuplicateTurnError,
    LLMError,
    LLMProtocolError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    LLMClient,
)
from tools.fake_llm_server import FakeLLMServer, REPLY


def make_config(**overrides) -> LLMConfig:
    base = dict(
        provider="openai_compat",
        base_url="http://127.0.0.1:1/v1",
        api_key="test-key",
        model="fake-model",
        stream=True,
        max_attempts=3,
        backoff_base_s=0.01,
        backoff_max_s=0.05,
        total_timeout_s=10.0,
        request_timeout_s=5.0,
    )
    base.update(overrides)
    return LLMConfig(**base)


MSGS = [ChatMessage("system", "你是测试角色"), ChatMessage("user", "你好")]


@pytest.fixture()
def server_factory():
    servers: list[FakeLLMServer] = []

    def _make(scenario: str = "stream", delay_s: float = 0.0) -> FakeLLMServer:
        srv = FakeLLMServer(scenario=scenario, delay_s=delay_s)
        srv.__enter__()
        servers.append(srv)
        return srv

    yield _make
    for srv in servers:
        srv.stop()


def test_stream_success(server_factory) -> None:
    srv = server_factory("stream")
    cfg = make_config(base_url=srv.base_url)

    async def go():
        deltas: list[str] = []
        async with LLMClient(cfg) as client:
            result = await client.chat(MSGS, turn_id="t1", on_delta=deltas.append)
        return result, deltas

    result, deltas = asyncio.run(go())
    assert result.text == REPLY
    assert "".join(deltas) == REPLY
    assert result.streamed is True
    assert result.first_token_ms is not None and result.first_token_ms >= 0
    assert result.total_ms >= result.first_token_ms
    assert result.finish_reason == "stop"
    assert result.delta_count == len(deltas)


def test_blocking_success(server_factory) -> None:
    srv = server_factory("stream")
    cfg = make_config(base_url=srv.base_url, stream=False)

    async def go():
        async with LLMClient(cfg) as client:
            return await client.chat(MSGS, turn_id="t2")

    result = asyncio.run(go())
    assert result.text == REPLY
    assert result.streamed is False
    assert result.usage["total_tokens"] == 120


def test_usage_in_stream_when_enabled(server_factory) -> None:
    srv = server_factory("stream")
    cfg = make_config(base_url=srv.base_url, stream_include_usage=True)

    async def go():
        async with LLMClient(cfg) as client:
            return await client.chat(MSGS, turn_id="t3")

    result = asyncio.run(go())
    assert result.usage.get("total_tokens") == 120


def test_auth_error_not_retried(server_factory) -> None:
    srv = server_factory("auth_error")
    cfg = make_config(base_url=srv.base_url)

    async def go():
        async with LLMClient(cfg) as client:
            await client.chat(MSGS, turn_id="t4")

    with pytest.raises(LLMAuthError) as exc_info:
        asyncio.run(go())
    assert exc_info.value.status_code == 401
    assert "invalid api key" in exc_info.value.message


def test_server_error_retried_then_fails(server_factory) -> None:
    srv = server_factory("server_error")
    cfg = make_config(base_url=srv.base_url, max_attempts=3)

    async def go():
        async with LLMClient(cfg) as client:
            await client.chat(MSGS, turn_id="t5")

    with pytest.raises(LLMServerError):
        asyncio.run(go())


def test_rate_limit_error(server_factory) -> None:
    srv = server_factory("rate_limit")
    cfg = make_config(base_url=srv.base_url, max_attempts=2)

    async def go():
        async with LLMClient(cfg) as client:
            await client.chat(MSGS, turn_id="t6")

    with pytest.raises(LLMRateLimitError):
        asyncio.run(go())


def test_bad_json_response(server_factory) -> None:
    srv = server_factory("bad_json")
    cfg = make_config(base_url=srv.base_url, stream=False)

    async def go():
        async with LLMClient(cfg) as client:
            await client.chat(MSGS, turn_id="t7")

    with pytest.raises(LLMProtocolError):
        asyncio.run(go())


def test_duplicate_turn_rejected(server_factory) -> None:
    srv = server_factory("stream")
    cfg = make_config(base_url=srv.base_url, stream=False)

    async def go():
        async with LLMClient(cfg) as client:
            await client.chat(MSGS, turn_id="dup")
            await client.chat(MSGS, turn_id="dup")

    with pytest.raises(LLMDuplicateTurnError):
        asyncio.run(go())


def test_reset_turn_allows_retry(server_factory) -> None:
    srv = server_factory("stream")
    cfg = make_config(base_url=srv.base_url, stream=False)

    async def go():
        async with LLMClient(cfg) as client:
            first = await client.chat(MSGS, turn_id="r1")
            client.reset_turn("r1")
            second = await client.chat(MSGS, turn_id="r1")
            return first, second

    first, second = asyncio.run(go())
    assert first.text == second.text == REPLY


def test_cancel_mid_stream(server_factory) -> None:
    srv = server_factory("stream", delay_s=0.05)
    cfg = make_config(base_url=srv.base_url)

    async def go():
        received: list[str] = []
        async with LLMClient(cfg) as client:
            async def run_chat():
                await client.chat(MSGS, turn_id="c1", on_delta=received.append)

            task = asyncio.create_task(run_chat())
            await asyncio.sleep(0.15)
            cancelled = await client.cancel()
            with pytest.raises((LLMCancelledError, asyncio.CancelledError)):
                await task
            return cancelled, received

    cancelled, received = asyncio.run(go())
    assert cancelled is True
    assert received  # 取消前确实收到过片段


def test_empty_messages_rejected(server_factory) -> None:
    srv = server_factory("stream")
    cfg = make_config(base_url=srv.base_url)

    async def go():
        async with LLMClient(cfg) as client:
            await client.chat([], turn_id="e1")

    with pytest.raises(LLMError):
        asyncio.run(go())


def test_connection_refused_is_network_error() -> None:
    cfg = make_config(base_url="http://127.0.0.1:9/v1", max_attempts=1)

    async def go():
        async with LLMClient(cfg) as client:
            await client.chat(MSGS, turn_id="n1")

    from server.llm import LLMNetworkError

    with pytest.raises(LLMNetworkError):
        asyncio.run(go())


def test_timeout_is_reported(server_factory) -> None:
    srv = server_factory("slow")
    cfg = make_config(
        base_url=srv.base_url, stream=False, request_timeout_s=0.3, total_timeout_s=5.0, max_attempts=1
    )

    async def go():
        async with LLMClient(cfg) as client:
            await client.chat(MSGS, turn_id="to1")

    with pytest.raises(LLMTimeoutError):
        asyncio.run(go())


def test_retry_then_success_with_mock_transport() -> None:
    """用 MockTransport 精确验证：先 500，第二次成功。"""
    calls: list[str] = []
    body_ok = json.dumps(
        {
            "model": "fake-model",
            "choices": [{"message": {"content": "第二次成功"}, "finish_reason": "stop"}],
        }
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return httpx.Response(200, content=body_ok, headers={"content-type": "application/json"})

    cfg = make_config(stream=False, max_attempts=3)

    async def go():
        transport = httpx.MockTransport(handler)
        async with LLMClient(cfg, transport=transport) as client:
            return await client.chat(MSGS, turn_id="m1")

    result = asyncio.run(go())
    assert result.text == "第二次成功"
    assert result.attempts == 2
    assert len(calls) == 2
    assert calls[0] == "/v1/chat/completions"


def test_authorization_header_sent_and_not_logged(caplog, server_factory) -> None:
    srv = server_factory("stream")
    cfg = make_config(base_url=srv.base_url, stream=False, api_key="sk-very-secret-value")

    async def go():
        async with LLMClient(cfg) as client:
            return await client.chat(MSGS, turn_id="h1")

    with caplog.at_level("DEBUG"):
        asyncio.run(go())
    assert "sk-very-secret-value" not in caplog.text
