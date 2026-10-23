"""云端 LLM 统一客户端。

覆盖文档第五节要求：
1. 地址/模型/密钥来自环境变量（``LLMConfig``）。
2. 角色卡注入由调用方拼进 messages，本模块只做协议层。
3. 最近对话历史由调用方传入。
4. 多轮对话无状态，由调用方维护。
5. 优先流式（``stream=True``），回调逐片段消费。
6. 记录首片段与完整回复耗时。
7. 超时/限流/网络失败/API 错误分类处理。
8. 日志不输出密钥（``redacted()`` + 日志中不打印 headers）。
9. 消息结构化，不做硬编码大字符串。
10. 支持取消当前轮次。
11. 有限次重试 + 指数退避。
12. 同一 turn_id 防重复调用。
13. 服务商差异由适配器吸收。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Iterable

import httpx

from .adapter import ChatAdapter, get_adapter
from .base import (
    ChatMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMCancelledError,
    LLMConfigError,
    LLMDuplicateTurnError,
    LLMError,
    LLMNetworkError,
    LLMProtocolError,
    LLMRateLimitError,
    LLMResult,
    LLMServerError,
    LLMTimeoutError,
)

logger = logging.getLogger("llm.client")

# 流式片段回调：同步函数即可，异常会被记录但不会中断本轮
DeltaCallback = Callable[[str], None]


class LLMClient:
    """供应商无关的 LLM 客户端。

    用法::

        async with LLMClient(config) as client:
            result = await client.chat(messages, turn_id="t-1", on_delta=print)
    """

    def __init__(
        self,
        config: Any,
        *,
        adapter: ChatAdapter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._adapter = adapter or get_adapter(getattr(config, "provider", "openai_compat"))
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

        # turn 级去重：防止同一轮意外重复调用云端（重复扣费）
        self._active_turns: set[str] = set()
        self._completed_turns: set[str] = set()
        # 当前进行中的请求任务，供 cancel() 使用
        self._current_task: asyncio.Task[Any] | None = None
        self._cancel_event = asyncio.Event()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "LLMClient":
        self._ensure_client()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            cfg = self._config
            timeout = httpx.Timeout(
                connect=cfg.connect_timeout_s,
                read=cfg.request_timeout_s,
                write=cfg.request_timeout_s,
                pool=cfg.connect_timeout_s,
            )
            self._client = httpx.AsyncClient(
                timeout=timeout,
                transport=self._transport,
                headers={
                    "Authorization": f"Bearer {cfg.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream" if cfg.stream else "application/json",
                },
                # 不使用系统代理猜测，避免企业代理导致意外转发
                trust_env=False,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: Iterable[ChatMessage],
        *,
        turn_id: str | None = None,
        on_delta: DeltaCallback | None = None,
        stream: bool | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        """执行一轮对话。

        :param messages: 已按顺序拼好的消息（系统提示词/角色卡 + 历史 + 用户输入）
        :param turn_id: 本轮唯一标识；相同 id 重复调用会被拒绝（防重复扣费）
        :param on_delta: 流式片段回调；传入则尽量流式
        :param stream: 覆盖配置中的流式开关
        :raises LLMConfigError: 配置缺失/非法
        :raises LLMAuthError: 密钥无效
        :raises LLMRateLimitError: 触发限流（已重试仍失败）
        :raises LLMTimeoutError: 超时（已重试仍失败）
        :raises LLMNetworkError: 网络不可达（已重试仍失败）
        :raises LLMServerError: 服务商 5xx（已重试仍失败）
        :raises LLMProtocolError: 返回格式不符合协议
        :raises LLMCancelledError: 本轮被 cancel() 取消
        :raises LLMDuplicateTurnError: turn_id 重复
        """
        cfg = self._config
        if hasattr(cfg, "validate"):
            cfg.validate()

        msg_list = list(messages)
        if not msg_list:
            raise LLMBadRequestError("消息列表为空")
        for msg in msg_list:
            if not isinstance(msg, ChatMessage):
                raise LLMBadRequestError("messages 中必须是 ChatMessage 实例")

        use_stream = cfg.stream if stream is None else bool(stream)
        if on_delta is not None and stream is None:
            use_stream = True

        turn_key = turn_id or f"auto-{uuid.uuid4().hex[:12]}"
        self._begin_turn(turn_key)

        # 每次调用使用独立取消事件，避免上一轮的取消影响本轮
        cancel_event = asyncio.Event()
        self._cancel_event = cancel_event
        task = asyncio.current_task()
        self._current_task = task

        started = time.perf_counter()
        try:
            result = await self._chat_with_retry(
                msg_list,
                turn_key=turn_key,
                on_delta=on_delta,
                use_stream=use_stream,
                temperature=cfg.temperature if temperature is None else temperature,
                max_tokens=cfg.max_tokens if max_tokens is None else max_tokens,
                started=started,
                cancel_event=cancel_event,
            )
            self._completed_turns.add(turn_key)
            return result
        except LLMCancelledError:
            raise
        finally:
            self._active_turns.discard(turn_key)
            if self._current_task is task:
                self._current_task = None

    async def cancel(self) -> bool:
        """取消当前进行中的轮次。返回是否确有请求被取消。"""
        self._cancel_event.set()
        task = self._current_task
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    def reset_turn(self, turn_id: str) -> None:
        """允许显式重试同一 turn_id（例如上层确认要重新生成）。"""
        self._completed_turns.discard(turn_id)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _begin_turn(self, turn_key: str) -> None:
        if turn_key in self._active_turns:
            raise LLMDuplicateTurnError(f"turn {turn_key!r} 正在执行，拒绝重复调用")
        if turn_key in self._completed_turns:
            raise LLMDuplicateTurnError(
                f"turn {turn_key!r} 已完成，拒绝重复调用（如确需重试请先 reset_turn）"
            )
        self._active_turns.add(turn_key)

    async def _chat_with_retry(
        self,
        messages: list[ChatMessage],
        *,
        turn_key: str,
        on_delta: DeltaCallback | None,
        use_stream: bool,
        temperature: float,
        max_tokens: int,
        started: float,
        cancel_event: asyncio.Event,
    ) -> LLMResult:
        cfg = self._config
        max_attempts = max(1, int(cfg.max_attempts))
        last_error: LLMError | None = None

        for attempt in range(1, max_attempts + 1):
            if cancel_event.is_set():
                raise LLMCancelledError("本轮已被取消")

            elapsed = time.perf_counter() - started
            if elapsed >= cfg.total_timeout_s:
                raise LLMTimeoutError(
                    f"超过整轮总超时 {cfg.total_timeout_s}s（已尝试 {attempt - 1} 次）"
                )

            emitted = False

            def guarded_delta(text: str, _attempt: int = attempt) -> None:
                nonlocal emitted
                emitted = True
                if on_delta is not None:
                    try:
                        on_delta(text)
                    except Exception:  # 回调异常不应中断本轮
                        logger.exception("流式回调抛出异常，已忽略")

            try:
                result = await self._single_request(
                    messages,
                    on_delta=guarded_delta,
                    use_stream=use_stream,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    started=started,
                    cancel_event=cancel_event,
                )
                result.attempts = attempt
                logger.info(
                    "llm ok turn=%s attempt=%d first_token_ms=%s total_ms=%.1f chars=%d",
                    turn_key,
                    attempt,
                    result.summary()["first_token_ms"],
                    result.total_ms,
                    len(result.text),
                )
                return result
            except LLMCancelledError:
                raise
            except LLMError as exc:
                last_error = exc
                logger.warning(
                    "llm error turn=%s attempt=%d/%d code=%s status=%s msg=%s",
                    turn_key,
                    attempt,
                    max_attempts,
                    exc.code,
                    exc.status_code,
                    exc.message,
                )
                # 已经吐字后再重试会导致重复文本，直接失败
                if emitted:
                    raise LLMError(
                        f"流式输出中途失败（已输出部分内容，不再重试）: {exc.message}",
                        status_code=exc.status_code,
                    ) from exc
                if not exc.retryable or attempt >= max_attempts:
                    raise

                delay = self._backoff_delay(attempt, exc.retry_after_s)
                remaining = cfg.total_timeout_s - (time.perf_counter() - started)
                if delay >= remaining:
                    raise LLMTimeoutError(
                        f"重试等待将超出整轮总超时 {cfg.total_timeout_s}s（上次错误: {exc.message}）"
                    ) from exc
                logger.info("llm retry turn=%s in %.2fs", turn_key, delay)
                await self._sleep_or_cancel(delay, cancel_event)

        assert last_error is not None  # pragma: no cover
        raise last_error

    def _backoff_delay(self, attempt: int, retry_after_s: float | None) -> float:
        cfg = self._config
        if retry_after_s is not None:
            return min(max(retry_after_s, 0.0), cfg.backoff_max_s)
        delay = cfg.backoff_base_s * (2 ** (attempt - 1))
        return min(delay, cfg.backoff_max_s)

    @staticmethod
    async def _sleep_or_cancel(delay: float, cancel_event: asyncio.Event) -> None:
        if delay <= 0:
            return
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return
        raise LLMCancelledError("等待重试期间本轮被取消")

    async def _single_request(
        self,
        messages: list[ChatMessage],
        *,
        on_delta: DeltaCallback | None,
        use_stream: bool,
        temperature: float,
        max_tokens: int,
        started: float,
        cancel_event: asyncio.Event,
    ) -> LLMResult:
        cfg = self._config
        client = self._ensure_client()
        payload = self._adapter.build_payload(
            model=cfg.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=use_stream,
            stream_include_usage=bool(getattr(cfg, "stream_include_usage", False)),
            thinking=str(getattr(cfg, "thinking", "default")),
        )
        url = cfg.endpoint_url
        request_id = uuid.uuid4().hex[:12]

        try:
            if use_stream:
                return await self._stream_request(
                    client, url, payload, on_delta, started, cancel_event, request_id
                )
            return await self._blocking_request(client, url, payload, started, cancel_event, request_id)
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(f"请求超时: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            raise LLMNetworkError(f"网络错误: {type(exc).__name__}: {exc}") from exc

    async def _blocking_request(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any],
        started: float,
        cancel_event: asyncio.Event,
        request_id: str,
    ) -> LLMResult:
        response = await client.post(url, json=payload)
        self._adapter.raise_for_status(response.status_code, response.content, dict(response.headers))
        first_token_ms = (time.perf_counter() - started) * 1000
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMProtocolError("响应不是合法 JSON") from exc
        text, finish_reason, usage = self._adapter.parse_response(body)
        total_ms = (time.perf_counter() - started) * 1000
        return LLMResult(
            text=text,
            model=str(body.get("model") or self._config.model),
            finish_reason=finish_reason,
            usage=usage,
            first_token_ms=first_token_ms,
            total_ms=total_ms,
            delta_count=1 if text else 0,
            streamed=False,
            request_id=request_id,
        )

    async def _stream_request(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any],
        on_delta: DeltaCallback | None,
        started: float,
        cancel_event: asyncio.Event,
        request_id: str,
    ) -> LLMResult:
        cfg = self._config
        chunks: list[str] = []
        first_token_ms: float | None = None
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        model = cfg.model
        delta_count = 0
        done = False

        async with client.stream("POST", url, json=payload) as response:
            if response.status_code >= 300:
                body = await response.aread()
                self._adapter.raise_for_status(
                    response.status_code, body, dict(response.headers)
                )
                raise LLMProtocolError(f"未预期的状态码 {response.status_code}")

            async for raw_line in response.aiter_lines():
                if cancel_event.is_set():
                    raise LLMCancelledError("本轮已被取消")
                if time.perf_counter() - started > cfg.total_timeout_s:
                    raise LLMTimeoutError(f"流式读取超过整轮总超时 {cfg.total_timeout_s}s")

                line = raw_line.strip()
                if not line or line.startswith(":"):
                    continue  # SSE 注释/心跳
                if not line.startswith("data:"):
                    continue  # 忽略 event:/id: 等字段
                data = line[len("data:"):].strip()
                if not data:
                    continue

                event = self._adapter.parse_stream_data(data)
                if event.kind == "delta" and event.text:
                    if first_token_ms is None:
                        first_token_ms = (time.perf_counter() - started) * 1000
                    chunks.append(event.text)
                    delta_count += 1
                    if on_delta is not None:
                        on_delta(event.text)
                    if event.finish_reason:
                        finish_reason = event.finish_reason
                elif event.kind == "finish":
                    if event.finish_reason and event.finish_reason != "done":
                        finish_reason = event.finish_reason
                    if event.usage:
                        usage = event.usage
                    if event.finish_reason == "done":
                        done = True
                elif event.kind == "usage" and event.usage:
                    usage = event.usage

                if done:
                    break

        total_ms = (time.perf_counter() - started) * 1000
        text = "".join(chunks)
        if not done and not text:
            raise LLMProtocolError("流式响应未返回任何文本且未正常结束")
        return LLMResult(
            text=text,
            model=model,
            finish_reason=finish_reason,
            usage=usage,
            first_token_ms=first_token_ms,
            total_ms=total_ms,
            delta_count=delta_count,
            streamed=True,
            request_id=request_id,
        )


__all__ = ["LLMClient", "DeltaCallback"]
