"""角色与声音管理的请求/响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 合法的情绪标签。
#: 依据决策 D4：3 条官方指令为正式支持，2 条自造标实验性。
OFFICIAL_EMOTIONS = ("neutral", "happy", "sad", "angry")
EXPERIMENTAL_EMOTIONS = ("comforting", "playful")
ALL_EMOTIONS = OFFICIAL_EMOTIONS + EXPERIMENTAL_EMOTIONS


# ----------------------------------------------------------------------
# 角色
# ----------------------------------------------------------------------


class CharacterCreate(BaseModel):
    """创建角色。"""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    character_id: str = Field(
        ..., min_length=1, max_length=64, description="角色唯一标识。"
    )
    display_name: str = Field(..., min_length=1, max_length=128, description="显示名。")
    description: str = Field(default="", max_length=2000)
    personality: str = Field(default="", max_length=2000)
    speaking_habits: str = Field(default="", max_length=2000)
    default_emotion: str = Field(default="neutral")
    default_style: str = Field(default="", max_length=64)
    speaker_id: str | None = Field(default=None, description="绑定的声音档案。")
    language: str = Field(default="zh", max_length=16)
    allowed_emotions: list[str] = Field(
        default_factory=lambda: list(OFFICIAL_EMOTIONS),
        description="允许的情绪标签。",
    )
    voice_instruction_template: str = Field(default="", max_length=500)
    model_id: str = Field(default="", max_length=64)
    model_version: str = Field(default="", max_length=64)
    config_version: str = Field(default="", max_length=64)

    @field_validator("character_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("character_id 不能为空")
        # 允许字母数字、下划线、连字符（要能安全放进 URL 和文件名）
        if not all(c.isalnum() or c in "_-" for c in v):
            raise ValueError("character_id 只能包含字母、数字、下划线和连字符")
        return v

    @field_validator("default_emotion")
    @classmethod
    def _check_default_emotion(cls, v: str) -> str:
        if v not in ALL_EMOTIONS:
            raise ValueError(
                f"default_emotion 必须是 {ALL_EMOTIONS} 之一，当前为 {v!r}"
            )
        return v

    @field_validator("allowed_emotions")
    @classmethod
    def _check_allowed_emotions(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("allowed_emotions 不能为空")
        unknown = [e for e in v if e not in ALL_EMOTIONS]
        if unknown:
            raise ValueError(
                f"未知情绪标签 {unknown}，合法值：{ALL_EMOTIONS}"
            )
        # 去重且保序
        seen: set[str] = set()
        out: list[str] = []
        for e in v:
            if e not in seen:
                seen.add(e)
                out.append(e)
        return out


class CharacterUpdate(BaseModel):
    """局部更新角色。所有字段可选。"""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    personality: str | None = Field(default=None, max_length=2000)
    speaking_habits: str | None = Field(default=None, max_length=2000)
    default_emotion: str | None = None
    default_style: str | None = Field(default=None, max_length=64)
    speaker_id: str | None = None
    language: str | None = Field(default=None, max_length=16)
    allowed_emotions: list[str] | None = None
    voice_instruction_template: str | None = Field(default=None, max_length=500)
    model_id: str | None = Field(default=None, max_length=64)
    model_version: str | None = Field(default=None, max_length=64)
    config_version: str | None = Field(default=None, max_length=64)

    @field_validator("default_emotion")
    @classmethod
    def _check_emotion(cls, v: str | None) -> str | None:
        if v is not None and v not in ALL_EMOTIONS:
            raise ValueError(f"default_emotion 必须是 {ALL_EMOTIONS} 之一")
        return v

    @field_validator("allowed_emotions")
    @classmethod
    def _check_allowed(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        if not v:
            raise ValueError("allowed_emotions 不能为空")
        unknown = [e for e in v if e not in ALL_EMOTIONS]
        if unknown:
            raise ValueError(f"未知情绪标签 {unknown}")
        return v


class CharacterOut(BaseModel):
    """角色响应。"""

    # model_id / model_version 与 pydantic 的 model_ 保护命名空间冲突，
    # 但这两个字段名来自需求文档，必须保留，因此关闭保护。
    model_config = ConfigDict(protected_namespaces=())

    character_id: str
    display_name: str
    description: str = ""
    personality: str = ""
    speaking_habits: str = ""
    default_emotion: str = "neutral"
    default_style: str = ""
    speaker_id: str | None = None
    language: str = "zh"
    allowed_emotions: list[str] = Field(default_factory=list)
    voice_instruction_template: str = ""
    model_id: str = ""
    model_version: str = ""
    config_version: str = ""
    created_at: str = ""
    updated_at: str = ""


# ----------------------------------------------------------------------
# 声音
# ----------------------------------------------------------------------


class SpeakerCreate(BaseModel):
    """创建声音档案。"""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    speaker_id: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    audio_path: str = Field(..., min_length=1, description="参考音频路径。")
    transcript: str = Field(
        ...,
        min_length=1,
        description=(
            "参考音频的逐字文本。零样本克隆必需，"
            "为空会导致音色跑偏，因此强制必填。"
        ),
    )
    consent_status: str = Field(default="unknown", max_length=32)
    source: str = Field(default="", max_length=500)
    model_version: str = Field(default="", max_length=64)
    preprocessing_version: str = Field(default="", max_length=64)

    @field_validator("speaker_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("speaker_id 不能为空")
        if not all(c.isalnum() or c in "_-" for c in v):
            raise ValueError("speaker_id 只能包含字母、数字、下划线和连字符")
        return v

    @field_validator("transcript")
    @classmethod
    def _check_transcript(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError(
                "transcript 不能为空。零样本克隆需要参考音频的逐字文本，"
                "空文本会导致合成音色严重跑偏。"
            )
        return v


class SpeakerUpdate(BaseModel):
    """局部更新声音档案。"""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    display_name: str | None = Field(default=None, max_length=128)
    audio_path: str | None = Field(default=None, min_length=1)
    transcript: str | None = Field(default=None, min_length=1)
    consent_status: str | None = Field(default=None, max_length=32)
    source: str | None = Field(default=None, max_length=500)
    model_version: str | None = Field(default=None, max_length=64)
    preprocessing_version: str | None = Field(default=None, max_length=64)


class SpeakerOut(BaseModel):
    """声音档案响应。"""

    model_config = ConfigDict(protected_namespaces=())

    speaker_id: str
    display_name: str = ""
    audio_path: str = ""
    transcript: str = ""
    sample_rate: int = 0
    channels: int = 0
    duration_s: float = 0.0
    quality_report: dict[str, Any] = Field(default_factory=dict)
    consent_status: str = "unknown"
    source: str = ""
    feature_cache_key: str = ""
    model_version: str = ""
    preprocessing_version: str = ""
    created_at: str = ""
    updated_at: str = ""


class QualityReportOut(BaseModel):
    """质量检测响应。"""

    path: str
    ok: bool
    score: float
    duration_s: float
    sample_rate: int
    channels: int
    rms_dbfs: float
    peak: float
    snr_db: float
    silence_ratio: float
    clip_ratio: float
    issues: list[dict[str, str]] = Field(default_factory=list)
    summary: str = ""


__all__ = [
    "ALL_EMOTIONS",
    "EXPERIMENTAL_EMOTIONS",
    "OFFICIAL_EMOTIONS",
    "CharacterCreate",
    "CharacterOut",
    "CharacterUpdate",
    "QualityReportOut",
    "SpeakerCreate",
    "SpeakerOut",
    "SpeakerUpdate",
]
