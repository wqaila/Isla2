"""本地 TTS 模块（HTTP 客户端，引擎无关）。"""

from .client import (
    TTSBadRequestError,
    TTSClient,
    TTSConfig,
    TTSDuplicateError,
    TTSError,
    TTSResult,
    TTSTimeoutError,
    TTSUnavailableError,
)

__all__ = [
    "TTSClient",
    "TTSConfig",
    "TTSResult",
    "TTSError",
    "TTSUnavailableError",
    "TTSTimeoutError",
    "TTSBadRequestError",
    "TTSDuplicateError",
]
