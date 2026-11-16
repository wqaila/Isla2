"""expression 包：表达控制（路线乙）。

D1 实测结论：CosyVoice3 的情绪指令无效。
因此本包只控制确定生效的维度：语速 / 音量 / 停顿。
情绪标签保留为元数据，由 LLM 侧用词表达。
"""

from .control import (
    EMOTION_LABELS,
    PAUSE_PROFILES,
    ExpressionParams,
    PauseProfile,
    build_params,
)
from .instruction import ParseResult, merge_params, parse_instruction

__all__ = [
    "EMOTION_LABELS",
    "PAUSE_PROFILES",
    "ExpressionParams",
    "ParseResult",
    "PauseProfile",
    "build_params",
    "merge_params",
    "parse_instruction",
]
