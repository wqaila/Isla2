"""云端 LLM 模块的公共类型与错误定义。

设计目标（对应方案文档第五节）：
- 业务代码只依赖本模块的类型与 ``LLMClient``，不直接依赖任何一家服务商 SDK。
- 服务商差异由 ``ChatAdapter`` 适配器吸收。
- 错误必须可分类（可重试 / 不可重试），供上层状态机决定恢复路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant"]

VALID_ROLES: frozenset[str] = frozenset({"system", "user", "assistant"})


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """一条对话消息。角色卡内容、历史、用户输入都通过它进入请求。"""

    role: Role
    content: str

    def __post_init__(self) -> None:
        if self.role not in VALID_ROLES:
            raise ValueError(f"非法消息角色: {self.role!r}")
        if not isinstance(self.content, str):
            raise ValueError("消息内容必须是字符串")

    def to_api(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class LLMDelta:
    """流式输出中的一个文本片段。"""

    text: str
    index: int
    elapsed_ms: float


@dataclass(slots=True)
class LLMResult:
    """一轮完整调用的结果与计时。

    计时口径与文档第十五节一致：
    - ``first_token_ms``：发出请求到收到首个文本片段（非流式时为收到完整响应）。
    - ``total_ms``：发出请求到本轮结束（含重试等待）。
    """

    text: str
    model: str
    finish_reason: str | None
    usage: dict[str, Any] = field(default_factory=dict)
    first_token_ms: float | None = None
    total_ms: float = 0.0
    attempts: int = 1
    delta_count: int = 0
    streamed: bool = False
    request_id: str | None = None

    @property
    def empty(self) -> bool:
        return not self.text.strip()

    def summary(self) -> dict[str, Any]:
        """用于日志/性能报告的结构化摘要，不含任何凭据。"""
        return {
            "model": self.model,
            "finish_reason": self.finish_reason,
            "usage": self.usage,
            "first_token_ms": round(self.first_token_ms, 1) if self.first_token_ms is not None else None,
            "total_ms": round(self.total_ms, 1),
            "attempts": self.attempts,
            "delta_count": self.delta_count,
            "streamed": self.streamed,
            "chars": len(self.text),
        }


# --------------------------------------------------------------------------
# 错误体系
# --------------------------------------------------------------------------


class LLMError(Exception):
    """LLM 模块统一异常基类。

    ``retryable`` 只表示"网络/服务端层面重试可能成功"，
    是否真的重试由 ``LLMClient`` 依据"是否已经输出过文本片段"等条件决定。
    """

    code: str = "llm_error"
    retryable: bool = False
    http_status: int | None = None

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_s: float | None = None,
        provider_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.retry_after_s = retry_after_s
        self.provider_code = provider_code

    def to_dict(self) -> dict[str, Any]:
        """可直接放进 WebSocket error 消息的结构（不含密钥）。"""
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.provider_code:
            payload["provider_code"] = self.provider_code
        return payload

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志可读性
        suffix = f" (status={self.status_code})" if self.status_code else ""
        return f"[{self.code}] {self.message}{suffix}"


class LLMConfigError(LLMError):
    code = "config_error"

    def __init__(self, message: str, *, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing = missing or []

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        if self.missing:
            payload["missing"] = list(self.missing)
        return payload


class LLMAuthError(LLMError):
    code = "auth_error"


class LLMRateLimitError(LLMError):
    code = "rate_limited"
    retryable = True


class LLMTimeoutError(LLMError):
    code = "timeout"
    retryable = True


class LLMNetworkError(LLMError):
    code = "network_error"
    retryable = True


class LLMServerError(LLMError):
    code = "server_error"
    retryable = True


class LLMBadRequestError(LLMError):
    code = "bad_request"


class LLMProtocolError(LLMError):
    code = "protocol_error"


class LLMCancelledError(LLMError):
    code = "cancelled"


class LLMDuplicateTurnError(LLMError):
    """同一 turn_id 被重复调用，用于防止意外重复扣费。"""

    code = "duplicate_turn"


__all__ = [
    "ChatMessage",
    "LLMDelta",
    "LLMResult",
    "Role",
    "VALID_ROLES",
    "LLMError",
    "LLMConfigError",
    "LLMAuthError",
    "LLMRateLimitError",
    "LLMTimeoutError",
    "LLMNetworkError",
    "LLMServerError",
    "LLMBadRequestError",
    "LLMProtocolError",
    "LLMCancelledError",
    "LLMDuplicateTurnError",
]
