"""服务商适配器：把"统一请求"翻译成具体协议，把"具体响应"翻译回统一类型。

第一版只实现 OpenAI ``/chat/completions`` 兼容协议（DeepSeek、Moonshot、智谱、
百炼兼容模式、SiliconFlow 等均属此类）。新增服务商只需实现 ``ChatAdapter``
子类并注册，业务代码无需改动（文档第五节第 13 条）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .base import (
    ChatMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMProtocolError,
    LLMRateLimitError,
    LLMServerError,
    LLMError,
)

# 适配器注册表：provider 名称 -> 适配器类
_ADAPTERS: dict[str, type["ChatAdapter"]] = {}


def register_adapter(name: str):
    def decorator(cls: type["ChatAdapter"]) -> type["ChatAdapter"]:
        _ADAPTERS[name] = cls
        return cls

    return decorator


def get_adapter(name: str) -> "ChatAdapter":
    try:
        cls = _ADAPTERS[name]
    except KeyError:
        available = ", ".join(sorted(_ADAPTERS)) or "(无)"
        raise LLMProtocolError(
            f"未知的 LLM 适配器 {name!r}，可用: {available}"
        ) from None
    return cls()


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """从一行 SSE 数据解析出的统一事件。

    ``kind``：
    - ``delta``：文本片段，内容在 ``text``
    - ``finish``：结束原因，内容在 ``finish_reason``
    - ``usage``：token 统计，内容在 ``usage``
    - ``ignore``：心跳/空行/未知字段，调用方可跳过
    """

    kind: str
    text: str = ""
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None


class ChatAdapter:
    """适配器接口。"""

    name = "base"

    def build_payload(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int,
        stream: bool,
        stream_include_usage: bool,
        thinking: str = "default",
    ) -> dict[str, Any]:
        raise NotImplementedError

    def parse_stream_data(self, data: str) -> StreamEvent:
        """解析 SSE ``data:`` 后的载荷（不含 ``data: `` 前缀）。"""
        raise NotImplementedError

    def parse_response(self, payload: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
        """解析非流式响应，返回 (文本, 结束原因, usage)。"""
        raise NotImplementedError

    # ---- 错误映射（可被具体适配器覆盖）----

    def raise_for_status(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        """HTTP 非 2xx 时抛出对应异常；2xx 时直接返回。"""
        if 200 <= status < 300:
            return
        message, provider_code = self._extract_error(body)
        retry_after = _parse_retry_after(headers.get("retry-after"))
        text = message or f"服务商返回 HTTP {status}"

        if status in (401, 403):
            raise LLMAuthError(text, status_code=status, provider_code=provider_code)
        if status == 429:
            raise LLMRateLimitError(
                text, status_code=status, retry_after_s=retry_after, provider_code=provider_code
            )
        if status == 408:
            raise LLMError(text, status_code=status, provider_code=provider_code)
        if 500 <= status < 600:
            raise LLMServerError(
                text, status_code=status, retry_after_s=retry_after, provider_code=provider_code
            )
        if status in (400, 404, 409, 413, 415, 422):
            raise LLMBadRequestError(text, status_code=status, provider_code=provider_code)
        raise LLMProtocolError(text, status_code=status, provider_code=provider_code)

    @staticmethod
    def _extract_error(body: bytes) -> tuple[str, str | None]:
        """尽力从错误响应体里提取可读信息；失败时返回空字符串。"""
        if not body:
            return "", None
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            snippet = body.decode("utf-8", errors="replace").strip()[:300]
            return snippet, None
        if not isinstance(payload, dict):
            return "", None
        err = payload.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("msg") or ""
            code = err.get("code") or err.get("type")
            return str(msg)[:500], (str(code) if code else None)
        if isinstance(err, str):
            return err[:500], None
        for key in ("message", "msg", "detail"):
            if isinstance(payload.get(key), str):
                return str(payload[key])[:500], None
        return "", None


@register_adapter("openai_compat")
class OpenAICompatAdapter(ChatAdapter):
    """OpenAI /chat/completions 兼容协议。"""

    name = "openai_compat"

    def build_payload(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int,
        stream: bool,
        stream_include_usage: bool,
        thinking: str = "default",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_api() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        # 部分服务商不支持 stream_options，因此默认不发送（见 .env.example 注释）
        if stream and stream_include_usage:
            payload["stream_options"] = {"include_usage": True}
        # DeepSeek V4 系列默认开启思考，会显著推高首字延迟。
        # 该字段为 DeepSeek 扩展；其它 OpenAI 兼容服务商应保持 "default"（不发送）。
        if thinking in ("disabled", "enabled"):
            payload["thinking"] = {"type": thinking}
        return payload

    def parse_stream_data(self, data: str) -> StreamEvent:
        data = data.strip()
        if not data:
            return StreamEvent("ignore")
        if data == "[DONE]":
            return StreamEvent("finish", finish_reason="done")

        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LLMProtocolError(f"流式数据不是合法 JSON: {data[:200]!r}") from exc

        if not isinstance(payload, dict):
            raise LLMProtocolError(f"流式数据格式异常: {data[:200]!r}")

        # 有的服务商在流中直接返回错误对象
        if payload.get("error"):
            message, code = ChatAdapter._extract_error(data.encode("utf-8"))
            raise LLMProtocolError(message or "流式响应中返回错误", provider_code=code)

        usage = payload.get("usage")
        choices = payload.get("choices")

        # 开启 include_usage 时，末尾会有一个 choices 为空、仅含 usage 的块
        if not choices:
            if isinstance(usage, dict) and usage:
                return StreamEvent("usage", usage=usage)
            return StreamEvent("ignore")

        if not isinstance(choices, list):
            raise LLMProtocolError("流式响应 choices 字段类型异常")

        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or choice.get("message") or {}
        finish_reason = choice.get("finish_reason")

        text = ""
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                # 少数服务商返回分段内容数组
                parts = []
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                text = "".join(parts)

        if text:
            return StreamEvent("delta", text=text, finish_reason=finish_reason)
        if finish_reason:
            return StreamEvent(
                "finish",
                finish_reason=str(finish_reason),
                usage=usage if isinstance(usage, dict) else None,
            )
        if isinstance(usage, dict) and usage:
            return StreamEvent("usage", usage=usage)
        return StreamEvent("ignore")

    def parse_response(self, payload: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
        if not isinstance(payload, dict):
            raise LLMProtocolError("响应体不是 JSON 对象")
        if payload.get("error"):
            message, code = ChatAdapter._extract_error(
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
            )
            raise LLMProtocolError(message or "服务商返回错误", provider_code=code)

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMProtocolError("响应缺少 choices 字段")
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else ""
        if isinstance(content, list):
            content = "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            )
        if not isinstance(content, str):
            content = ""
        finish_reason = choice.get("finish_reason")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return content, (str(finish_reason) if finish_reason else None), dict(usage)


def _parse_retry_after(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


__all__ = [
    "ChatAdapter",
    "OpenAICompatAdapter",
    "StreamEvent",
    "register_adapter",
    "get_adapter",
]
