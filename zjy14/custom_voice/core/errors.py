"""异常体系。

设计原则：
1. 每个异常带机器可读的 ``code`` 与人类可读的 ``message``。
2. 每个异常映射到明确的 HTTP 状态码，由 ``server.app`` 统一处理。
3. 响应体不泄露堆栈。
"""

from __future__ import annotations

from typing import Any


class ZJY14Error(Exception):
    """所有项目异常的基类。"""

    code: str = "internal_error"
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


# ---- 配置 ----


class ConfigError(ZJY14Error):
    """配置文件缺失、格式错误、字段未知或值越界。"""

    code = "config_error"
    http_status = 500


# ---- 引擎 ----


class EngineError(ZJY14Error):
    """引擎层通用错误。"""

    code = "engine_error"
    http_status = 500


class EngineNotReadyError(EngineError):
    """模型尚未加载完成。

    对应 HTTP 503 —— 这是 /health 在模型未就绪时必须返回的状态，
    绝不允许假报 200 OK。
    """

    code = "engine_not_ready"
    http_status = 503


class SynthesisError(EngineError):
    """合成过程失败。"""

    code = "synthesis_error"
    http_status = 500


class InvalidRequestError(EngineError):
    """请求参数不合法。

    对应 HTTP 400 —— zjy13 的 TTSClient 依赖这个状态码抛 TTSBadRequestError。
    """

    code = "invalid_request"
    http_status = 400


# ---- 声音 ----


class SpeakerError(ZJY14Error):
    """声音档案相关错误。"""

    code = "speaker_error"
    http_status = 500


class SpeakerNotFoundError(SpeakerError):
    code = "speaker_not_found"
    http_status = 404


class AudioQualityError(SpeakerError):
    """参考音频质量不达标（静音、削波、过短等）。"""

    code = "audio_quality_error"
    http_status = 422


# ---- 显存 ----


class VRAMError(ZJY14Error):
    """显存不足。

    对应 HTTP 507 Insufficient Storage —— 明确告知调用方是资源问题，
    而不是让服务无提示崩溃。
    """

    code = "vram_insufficient"
    http_status = 507


__all__ = [
    "AudioQualityError",
    "ConfigError",
    "EngineError",
    "EngineNotReadyError",
    "InvalidRequestError",
    "SpeakerError",
    "SpeakerNotFoundError",
    "SynthesisError",
    "VRAMError",
    "ZJY14Error",
]
