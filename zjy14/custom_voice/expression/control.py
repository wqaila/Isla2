"""表达控制：结构化参数定义与映射。

D1 实测结论（2026-11-16）：
  CosyVoice3-0.5B 的**情绪指令无效**。
  加入「请用蓝色的语气」这种无意义指令产生的 F0 变化（15.4/72.5 Hz）
  比真正的情绪指令（10.1/24.3 Hz）还大。

因此本模块采用路线乙：
  1. 只控制**确定生效**的维度：speed / volume / pause
  2. emotion 保留为**元数据**，供 LLM 侧用词表达，不传给 TTS 模型
  3. style 同理，仅作记录

实测依据（_p2b_speed.py，每组 3 次采样中位数）：
  speed 0.8 -> 7.64s   +20.1%
  speed 0.9 -> 6.04s   -5.0%
  speed 1.0 -> 6.36s    基准
  speed 1.1 -> 5.22s   -17.9%
  speed 1.2 -> 4.86s   -23.6%
  时长跨度 57%，F0 跨度 26.2Hz（音色有轻微变化，建议限制在 0.85~1.15）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: 情绪标签。仅作元数据，不映射到模型指令。
EMOTION_LABELS = (
    "neutral",
    "happy",
    "sad",
    "angry",
    "comforting",
    "playful",
)

#: 安全语速区间。
#: 实测 speed 0.8 与 1.2 会让 F0 偏移 26Hz，超出建议范围时记录警告。
SAFE_SPEED_MIN = 0.85
SAFE_SPEED_MAX = 1.15

#: 硬性语速区间（超出直接拒绝）。
HARD_SPEED_MIN = 0.5
HARD_SPEED_MAX = 2.0

#: 音量安全区间（线性增益）。
SAFE_VOLUME_MIN = 0.5
SAFE_VOLUME_MAX = 1.5

#: 停顿档位 -> 句间静音毫秒数。
#: 标点本身对停顿影响很小（实测静音占比 10.3%~18.0%），
#: 因此显式停顿靠「分句合成 + 插入静音」实现。
PAUSE_PROFILES: dict[str, int] = {
    "none": 0,        # 不分句，整段合成
    "tight": 80,      # 紧凑
    "natural": 180,   # 自然（默认）
    "relaxed": 350,   # 舒缓
    "dramatic": 600,  # 戏剧化停顿
}


class PauseProfile(str, Enum):
    """停顿档位。"""

    NONE = "none"
    TIGHT = "tight"
    NATURAL = "natural"
    RELAXED = "relaxed"
    DRAMATIC = "dramatic"


@dataclass(slots=True)
class ExpressionParams:
    """结构化表达参数。

    这是引擎实际消费的完整参数集。情绪标签只是元数据。
    """

    # ---- 真正生效的控制 ----
    speed: float = 1.0
    volume: float = 1.0
    pause_profile: PauseProfile = PauseProfile.NATURAL

    # ---- 元数据（不传给模型）----
    emotion: str = "neutral"
    intensity: float = 0.5
    style: str = ""

    # ---- 诊断信息（由解析器填充）----
    warnings: list[str] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "speed": self.speed,
            "volume": self.volume,
            "pause_profile": self.pause_profile.value,
            "pause_ms": PAUSE_PROFILES[self.pause_profile.value],
            "emotion": self.emotion,
            "intensity": self.intensity,
            "style": self.style,
            "warnings": list(self.warnings),
            "source": self.source,
        }

    def effective_controls(self) -> dict[str, Any]:
        """返回真正会传给引擎的参数（不含元数据）。

        用于日志与调试：明确区分「生效的」与「仅记录的」。
        """
        return {
            "speed": self.speed,
            "volume": self.volume,
            "pause_ms": PAUSE_PROFILES[self.pause_profile.value],
        }

    def metadata_only(self) -> dict[str, Any]:
        """返回仅作元数据的字段。"""
        return {
            "emotion": self.emotion,
            "intensity": self.intensity,
            "style": self.style,
            "note": "情绪/风格不传给 TTS 模型（D1 实测无效），仅供 LLM 侧与 UI 使用",
        }


def clamp_speed(speed: float) -> tuple[float, str | None]:
    """把语速限制在硬性区间，并对超出安全区间的值给出警告。

    :return: (限制后的值, 警告信息或 None)
    """
    original = speed
    if speed < HARD_SPEED_MIN:
        speed = HARD_SPEED_MIN
    elif speed > HARD_SPEED_MAX:
        speed = HARD_SPEED_MAX

    warn = None
    if not (SAFE_SPEED_MIN <= speed <= SAFE_SPEED_MAX):
        warn = (
            f"语速 {original:.2f} 超出建议区间 "
            f"[{SAFE_SPEED_MIN}, {SAFE_SPEED_MAX}]，"
            "实测会导致音色轻微漂移（F0 偏移约 26Hz）"
        )
    return speed, warn


def clamp_volume(volume: float) -> tuple[float, str | None]:
    """把音量限制在安全区间。"""
    original = volume
    if volume < SAFE_VOLUME_MIN:
        volume = SAFE_VOLUME_MIN
    elif volume > SAFE_VOLUME_MAX:
        volume = SAFE_VOLUME_MAX
    warn = None
    if abs(volume - original) > 1e-6:
        warn = (
            f"音量 {original:.2f} 超出安全区间 "
            f"[{SAFE_VOLUME_MIN}, {SAFE_VOLUME_MAX}]，已限制为 {volume:.2f}"
        )
    return volume, warn


def normalize_emotion(label: str | None) -> str:
    """归一化情绪标签。未知标签回退 neutral。"""
    if not label:
        return "neutral"
    low = label.strip().lower()
    return low if low in EMOTION_LABELS else "neutral"


def build_params(
    *,
    speed: float = 1.0,
    volume: float = 1.0,
    pause_profile: str | PauseProfile = PauseProfile.NATURAL,
    emotion: str | None = None,
    intensity: float | None = None,
    style: str | None = None,
    source: str = "",
) -> ExpressionParams:
    """构造表达参数，自动做区间校验与归一化。"""
    warnings: list[str] = []

    sp, w = clamp_speed(float(speed))
    if w:
        warnings.append(w)

    vol, w = clamp_volume(float(volume))
    if w:
        warnings.append(w)

    if isinstance(pause_profile, PauseProfile):
        pp = pause_profile
    else:
        raw = (pause_profile or "natural").strip().lower()
        if raw not in PAUSE_PROFILES:
            warnings.append(
                f"未知停顿档位 {pause_profile!r}，回退 natural。"
                f"合法值：{list(PAUSE_PROFILES)}"
            )
            pp = PauseProfile.NATURAL
        else:
            pp = PauseProfile(raw)

    emo = normalize_emotion(emotion)
    if emotion and emo != emotion.strip().lower():
        warnings.append(
            f"未知情绪标签 {emotion!r}，回退 neutral。合法值：{list(EMOTION_LABELS)}"
        )

    inten = 0.5 if intensity is None else float(intensity)
    inten = max(0.0, min(1.0, inten))

    return ExpressionParams(
        speed=sp,
        volume=vol,
        pause_profile=pp,
        emotion=emo,
        intensity=inten,
        style=(style or "").strip(),
        warnings=warnings,
        source=source,
    )


__all__ = [
    "EMOTION_LABELS",
    "HARD_SPEED_MAX",
    "HARD_SPEED_MIN",
    "PAUSE_PROFILES",
    "SAFE_SPEED_MAX",
    "SAFE_SPEED_MIN",
    "SAFE_VOLUME_MAX",
    "SAFE_VOLUME_MIN",
    "ExpressionParams",
    "PauseProfile",
    "build_params",
    "clamp_speed",
    "clamp_volume",
    "normalize_emotion",
]
