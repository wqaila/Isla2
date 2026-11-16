"""自然语言声音指令解析器。

把「温柔一点、放慢速度、像在安慰人」这类自然语言，解析成结构化参数。

设计原则（对应原提示词）：
  - 只解析**真正能生效**的维度（speed / volume / pause）
  - 情绪词（温柔、开心）不映射到模型指令，而是作为元数据传出
  - 解析不确定时给出 warnings，不静默猜测
  - 中文优先，兼顾少量英文

实现方式：规则匹配，不依赖 LLM。
理由：解析延迟必须极低（语音交互场景），且规则可测、可解释。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .control import (
    ExpressionParams,
    PauseProfile,
    build_params,
)

#: 语速关键词 -> 倍率
SPEED_KEYWORDS: dict[str, float] = {
    # 慢
    "慢": 0.88, "放慢": 0.85, "缓慢": 0.85, "慢慢": 0.88,
    "慢一点": 0.88, "慢一些": 0.88, "拖长": 0.85,
    "舒缓": 0.90, "从容": 0.92, "不着急": 0.90,
    "slow": 0.85, "slower": 0.85,
    # 快
    "快": 1.12, "加快": 1.15, "快速": 1.15, "快点": 1.12,
    "快一点": 1.12, "快一些": 1.12, "急促": 1.15,
    "fast": 1.15, "faster": 1.15,
    # 正常
    "正常速度": 1.0, "常速": 1.0, "正常": 1.0,
    "normal": 1.0,
}

#: 音量关键词 -> 倍率
VOLUME_KEYWORDS: dict[str, float] = {
    "大声": 1.25, "响一点": 1.25, "提高音量": 1.25, "音量高": 1.2,
    "loud": 1.25, "louder": 1.25,
    "小声": 0.78, "轻一点": 0.8, "轻声": 0.78, "压低": 0.8,
    "音量低": 0.8, "低语": 0.75,
    "soft": 0.8, "quieter": 0.8,
    "正常音量": 1.0,
}

#: 停顿关键词 -> 档位
PAUSE_KEYWORDS: dict[str, PauseProfile] = {
    "停顿长": PauseProfile.RELAXED,
    "停顿多一点": PauseProfile.RELAXED,
    "多停顿": PauseProfile.RELAXED,
    "留白": PauseProfile.RELAXED,
    "慢慢说": PauseProfile.RELAXED,
    "断句": PauseProfile.RELAXED,
    "戏剧": PauseProfile.DRAMATIC,
    "沉重": PauseProfile.DRAMATIC,
    "拖沓": PauseProfile.DRAMATIC,
    "紧凑": PauseProfile.TIGHT,
    "别停": PauseProfile.TIGHT,
    "连着说": PauseProfile.TIGHT,
    "一口气": PauseProfile.TIGHT,
    "自然": PauseProfile.NATURAL,
    "正常停顿": PauseProfile.NATURAL,
}

#: 情绪关键词 -> 标签
EMOTION_KEYWORDS: dict[str, str] = {
    "开心": "happy", "高兴": "happy", "快乐": "happy", "愉快": "happy",
    "兴奋": "happy", "欢喜": "happy", "happy": "happy",
    "难过": "sad", "伤心": "sad", "悲伤": "sad", "低落": "sad",
    "沮丧": "sad", "失落": "sad", "sad": "sad",
    "生气": "angry", "愤怒": "angry", "恼火": "angry", "不满": "angry",
    "angry": "angry",
    "安慰": "comforting", "温柔": "comforting", "暖心": "comforting",
    "关切": "comforting", "体贴": "comforting", "抚慰": "comforting",
    "comforting": "comforting",
    "俏皮": "playful", "活泼": "playful", "调皮": "playful",
    "轻快": "playful", "可爱": "playful", "playful": "playful",
    "中性": "neutral", "平静": "neutral", "正常": "neutral",
    "neutral": "neutral",
}

#: 强度修饰词 -> 强度调整
INTENSITY_KEYWORDS: dict[str, float] = {
    "非常": 0.9, "特别": 0.9, "极其": 0.95, "超级": 0.95,
    "很": 0.75, "挺": 0.7,
    "稍微": 0.3, "略微": 0.3, "一点点": 0.25, "有点": 0.35,
    "比较": 0.55,
}


@dataclass(slots=True)
class ParseResult:
    """解析结果。"""

    params: ExpressionParams
    matched: dict[str, str]
    unmatched: list[str]

    def to_dict(self) -> dict:
        return {
            "params": self.params.to_dict(),
            "effective": self.params.effective_controls(),
            "matched": self.matched,
            "unmatched": self.unmatched,
        }


def _find_first(text: str, table: dict[str, object]) -> tuple[str, object] | None:
    """在文本里找第一个命中的关键词。

    按关键词长度降序匹配，避免「慢一点」被「慢」抢先匹配。
    """
    for kw in sorted(table, key=len, reverse=True):
        if kw in text:
            return kw, table[kw]
    return None


def parse_instruction(instruction: str) -> ParseResult:
    """解析自然语言指令。

    例：
        "温柔一点，放慢速度"  -> speed 0.85, emotion comforting
        "大声一点，快一些"    -> volume 1.25, speed 1.12
        "像在安慰人，慢一点"  -> pause relaxed, speed 0.88, emotion comforting

    :return: ParseResult，含结构化参数、命中项、未识别项
    """
    text = (instruction or "").strip().lower()
    if not text:
        return ParseResult(
            params=build_params(source="empty"),
            matched={},
            unmatched=[],
        )

    matched: dict[str, str] = {}
    speed = 1.0
    volume = 1.0
    pause = PauseProfile.NATURAL
    emotion: str | None = None
    intensity: float | None = None

    # ---- 语速 ----
    hit = _find_first(text, SPEED_KEYWORDS)
    if hit:
        speed = float(hit[1])  # type: ignore[arg-type]
        matched["speed"] = hit[0]

    # ---- 音量 ----
    hit = _find_first(text, VOLUME_KEYWORDS)
    if hit:
        volume = float(hit[1])  # type: ignore[arg-type]
        matched["volume"] = hit[0]

    # ---- 停顿 ----
    hit = _find_first(text, PAUSE_KEYWORDS)
    if hit:
        pause = hit[1]  # type: ignore[assignment]
        matched["pause"] = hit[0]

    # ---- 情绪（仅元数据）----
    hit = _find_first(text, EMOTION_KEYWORDS)
    if hit:
        emotion = str(hit[1])
        matched["emotion"] = hit[0]

    # ---- 强度 ----
    hit = _find_first(text, INTENSITY_KEYWORDS)
    if hit:
        intensity = float(hit[1])  # type: ignore[arg-type]
        matched["intensity"] = hit[0]

    # ---- 未识别部分 ----
    # 去掉所有已命中的关键词，看还剩什么
    residual = text
    for kw in matched.values():
        residual = residual.replace(kw, "")
    residual = re.sub(r"[，。、；：,.;:\s]+", "", residual)
    unmatched = [residual] if residual else []

    params = build_params(
        speed=speed,
        volume=volume,
        pause_profile=pause,
        emotion=emotion,
        intensity=intensity,
        style="",
        source="instruction",
    )

    # 有未识别内容时提示（不猜测，避免静默错误）
    if unmatched:
        params.warnings.append(
            f"指令中有未识别部分：{residual!r}。"
            "已解析的控制项仍会生效，未识别部分被忽略。"
        )

    return ParseResult(params=params, matched=matched, unmatched=unmatched)


def merge_params(
    base: ExpressionParams,
    override: ExpressionParams,
) -> ExpressionParams:
    """合并两组参数。override 中「非默认值」的字段优先。

    用于：请求里同时给了 emotion 和 instruction 时，
    以 instruction 解析出的显式控制为准。
    """
    speed = override.speed if abs(override.speed - 1.0) > 1e-6 else base.speed
    volume = override.volume if abs(override.volume - 1.0) > 1e-6 else base.volume
    pause = (
        override.pause_profile
        if override.pause_profile is not PauseProfile.NATURAL
        else base.pause_profile
    )
    emotion = override.emotion if override.emotion != "neutral" else base.emotion
    intensity = (
        override.intensity if abs(override.intensity - 0.5) > 1e-6 else base.intensity
    )

    merged = build_params(
        speed=speed,
        volume=volume,
        pause_profile=pause,
        emotion=emotion,
        intensity=intensity,
        style=override.style or base.style,
        source=f"{base.source}+{override.source}",
    )
    # 保留两边的警告，便于排查
    merged.warnings = list(base.warnings) + list(override.warnings)
    return merged


__all__ = [
    "EMOTION_KEYWORDS",
    "INTENSITY_KEYWORDS",
    "PAUSE_KEYWORDS",
    "SPEED_KEYWORDS",
    "VOLUME_KEYWORDS",
    "ParseResult",
    "merge_params",
    "parse_instruction",
]
