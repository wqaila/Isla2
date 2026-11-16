"""请求/响应数据结构。

关键约束（D2）：``/synthesize`` 的请求与响应必须兼容 zjy13 的 TTSClient。
新增字段全部可选，旧调用不传时走默认值。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: zjy13 TTSClient 依赖的响应头。改这里等于改契约，必须同步 zjy13。
SYNTHESIS_RESPONSE_HEADERS = (
    "x-sample-rate",
    "x-duration",
    "x-total-ms",
    "x-rtf",
    "x-engine",
    "x-voice-id",
)


class SynthesisRequest(BaseModel):
    """合成请求。

    契约约束（D2）：前三个字段与 zjy13 的 TTSClient 完全兼容。
    其余字段可选，旧调用不传时走默认值。

    **重要（D1 结论）**：``emotion`` / ``intensity`` / ``style`` 仅作元数据，
    不传给 TTS 模型 —— 实测 CosyVoice3 的情绪指令无效。
    真正生效的是 ``speed`` / ``volume`` / ``pause_profile`` / ``instruction``。
    """

    model_config = ConfigDict(extra="forbid")

    # ---- zjy13 兼容字段 ----
    text: str = Field(..., min_length=1, max_length=2000, description="待合成文本。")
    voice_id: str = Field(default="elysia", description="音色标识。")
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="语速倍率。")

    # ---- 路线乙：真正生效的控制 ----
    volume: float = Field(
        default=1.0,
        ge=0.5,
        le=1.5,
        description="音量增益。在音频层应用，不依赖模型。",
    )
    pause_profile: str = Field(
        default="natural",
        description=(
            "停顿档位：none / tight / natural / relaxed / dramatic。"
            "通过分句合成 + 插入静音实现。"
        ),
    )
    instruction: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "自然语言声音指令，如「温柔一点，放慢速度」。"
            "由规则解析器转成 speed/volume/pause。"
        ),
    )

    # ---- 元数据（不传给模型，仅供 LLM 侧与 UI）----
    emotion: str | None = Field(
        default=None,
        description=(
            "情绪标签。**仅记录，不影响合成** —— "
            "实测 CosyVoice3 情绪指令无效（见 docs/13_D1_EMOTION_VERDICT.md）。"
        ),
    )
    intensity: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="情绪强度。仅记录，不影响合成。",
    )
    style: str | None = Field(
        default=None,
        description="表达风格。仅记录，不影响合成。",
    )

    @field_validator("text")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("text 不能为空白")
        return stripped

    @field_validator("voice_id")
    @classmethod
    def _check_voice(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("voice_id 不能为空")
        return v


class AudioChunk(BaseModel):
    """一个音频块。Phase 1 只有一块（整段）。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    sequence: int = Field(..., ge=0, description="块序号，从 0 开始。")
    is_final: bool = Field(..., description="是否为最后一块。")
    pcm: bytes = Field(..., description="PCM S16LE 裸数据。")
    sample_rate: int = Field(..., gt=0, description="采样率。")
    channels: int = Field(default=1, ge=1, description="声道数。")


class SynthesisResult(BaseModel):
    """一次合成的完整结果。"""

    request_id: str
    sample_rate: int = Field(..., gt=0)
    channels: int = Field(default=1, ge=1)
    duration_s: float = Field(..., ge=0)
    total_ms: float = Field(..., ge=0)
    first_audio_ms: float | None = None
    rtf: float = Field(..., ge=0)
    engine: str
    voice_id: str
    wav_bytes: bytes = Field(..., description="完整 WAV 文件字节。")
    peak_vram_mb: float | None = None

    def response_headers(self) -> dict[str, str]:
        """构造 zjy13 期望的响应头。"""
        return {
            "x-sample-rate": str(self.sample_rate),
            "x-duration": f"{self.duration_s:.3f}",
            "x-total-ms": f"{self.total_ms:.1f}",
            "x-rtf": f"{self.rtf:.4f}",
            "x-engine": self.engine,
            "x-voice-id": self.voice_id,
        }


class EngineStats(BaseModel):
    """引擎状态快照，供 /health 与 /system/status 使用。"""

    kind: str
    ready: bool
    sample_rate: int
    device: str
    gpu_name: str = ""
    torch_version: str = ""
    load_s: float = 0.0
    warmup_s: float = 0.0
    voices: list[str] = Field(default_factory=list)
    peak_vram_mb: float | None = None


__all__ = [
    "SYNTHESIS_RESPONSE_HEADERS",
    "AudioChunk",
    "EngineStats",
    "SynthesisRequest",
    "SynthesisResult",
]
