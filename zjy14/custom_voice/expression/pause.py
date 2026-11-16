"""停顿控制：文本分句 + 音频拼接。

为什么需要它：
  实测（_p2b_capability.py）标点对停顿的影响很有限——
  「无标点 / 逗号 / 句号 / 长停顿 / 换行 / 省略号」六种写法的
  静音占比只在 10.3%~18.0% 之间，模型并不会因为多写一个句号就多停 500ms。

  而「分句合成 + 手动插入静音」效果明确：
  整段合成静音 10.7% -> 分句拼接（250ms 间隔）静音 29.5%。

因此停顿控制采用：按标点切分 -> 逐段合成 -> 插入指定静音 -> 拼接。

注意边界：
  - 分句太多会让每段过短，音色可能不稳定（文本长度比会低于 0.5）
  - 拼接处可能有轻微不连续，需做短交叉淡化
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

#: 分句标点。按优先级排列（先按句末切，再按逗号切）。
SENTENCE_END = "。！？!?"
CLAUSE_SEP = "，,；;、"

#: 单段最大字符数。超过则强制在逗号处再切。
MAX_SEGMENT_CHARS = 60

#: 单段最小字符数。低于此值不与前段合并（避免碎片）。
MIN_SEGMENT_CHARS = 4

#: 拼接处的交叉淡化时长（毫秒），避免爆音。
CROSSFADE_MS = 8


@dataclass(slots=True)
class Segment:
    """一个待合成的文本段。"""

    text: str
    #: 该段结束后要插入的静音毫秒数（最后一段为 0）
    trailing_pause_ms: int = 0

    def __post_init__(self) -> None:
        self.text = self.text.strip()


def split_text(
    text: str,
    *,
    max_chars: int = MAX_SEGMENT_CHARS,
    min_chars: int = MIN_SEGMENT_CHARS,
) -> list[str]:
    """把文本切成适合逐段合成的片段。

    策略：
      1. 先按句末标点切（。！？!?）
      2. 单段仍过长时，按逗号/分号再切
      3. 过短的片段与相邻片段合并，避免碎片化

    :return: 文本段列表（已去除首尾空白与空段）
    """
    raw = (text or "").strip()
    if not raw:
        return []

    # 一级：按句末标点切，保留标点
    parts = re.split(f"([{re.escape(SENTENCE_END)}])", raw)
    merged: list[str] = []
    buf = ""
    for p in parts:
        if not p:
            continue
        buf += p
        if p and p[0] in SENTENCE_END:
            merged.append(buf.strip())
            buf = ""
    if buf.strip():
        merged.append(buf.strip())

    # 二级：过长的段按逗号再切
    out: list[str] = []
    for seg in merged:
        if len(seg) <= max_chars:
            out.append(seg)
            continue
        sub = re.split(f"([{re.escape(CLAUSE_SEP)}])", seg)
        cur = ""
        for p in sub:
            if not p:
                continue
            cur += p
            if p and p[0] in CLAUSE_SEP and len(cur) >= max_chars * 0.6:
                out.append(cur.strip())
                cur = ""
        if cur.strip():
            out.append(cur.strip())

    # 三级：合并过短的段
    final: list[str] = []
    for seg in out:
        if not seg:
            continue
        if final and len(seg) < min_chars:
            final[-1] += seg
        else:
            final.append(seg)

    return [s for s in final if s]


def build_segments(
    text: str,
    pause_ms: int,
    *,
    max_chars: int = MAX_SEGMENT_CHARS,
) -> list[Segment]:
    """构造带停顿信息的段列表。

    :param text: 原始文本
    :param pause_ms: 段间静音毫秒数。0 表示不分句（返回单段）
    """
    if pause_ms <= 0:
        t = (text or "").strip()
        return [Segment(text=t)] if t else []

    pieces = split_text(text, max_chars=max_chars)
    if len(pieces) <= 1:
        return [Segment(text=p) for p in pieces]

    segs: list[Segment] = []
    for i, p in enumerate(pieces):
        # 最后一段不插入尾部静音
        trailing = 0 if i == len(pieces) - 1 else pause_ms
        segs.append(Segment(text=p, trailing_pause_ms=trailing))
    return segs


def crossfade_concat(
    chunks: list[np.ndarray],
    sample_rate: int,
    fade_ms: int = CROSSFADE_MS,
) -> np.ndarray:
    """带交叉淡化的拼接，避免段间爆音。

    重叠区长度 = fade，总长度 = 各段之和 - (段数-1) * fade。
    """
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    if len(chunks) == 1:
        return chunks[0].astype(np.float32)

    fade = max(1, int(sample_rate * fade_ms / 1000))
    out = chunks[0].astype(np.float32)

    for nxt in chunks[1:]:
        nxt = nxt.astype(np.float32)
        if len(out) >= fade and len(nxt) >= fade:
            # 末尾淡出 + 开头淡入，重叠区取加权和
            tail = out[-fade:]
            head = nxt[:fade]
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            blended = tail * (1.0 - ramp) + head * ramp
            out = np.concatenate([out[:-fade], blended, nxt[fade:]])
        else:
            out = np.concatenate([out, nxt])
    return out


def concat_with_pauses(
    audio_parts: list[np.ndarray],
    pauses_ms: list[int],
    sample_rate: int,
) -> np.ndarray:
    """按指定静音时长拼接音频段。

    :param audio_parts: 每段的音频
    :param pauses_ms: 每段之后的静音毫秒（长度应与 audio_parts 一致，最后一项通常为 0）
    """
    if not audio_parts:
        return np.zeros(0, dtype=np.float32)
    if len(audio_parts) == 1:
        return audio_parts[0].astype(np.float32)

    pieces: list[np.ndarray] = []
    for i, part in enumerate(audio_parts):
        pieces.append(part.astype(np.float32))
        if i < len(pauses_ms):
            ms = pauses_ms[i]
            if ms > 0:
                pieces.append(
                    np.zeros(int(sample_rate * ms / 1000), dtype=np.float32)
                )
    return np.concatenate(pieces)


def apply_gain(samples: np.ndarray, gain: float) -> np.ndarray:
    """应用音量增益，并做削波保护。

    超过 1.0 的增益可能导致削波。这里用 tanh 软限幅：
    输入先缩放到 tanh 的线性区间，再映射回 [-1, 1]，
    保证输出严格不超界，同时比硬裁剪听感更自然。

    注意：早期版本写成 `tanh(x*1.2)/tanh(1.2)*0.99`，
    该式在 x 接近 1 时输出会超过 1.0（实测 1.098），是错的。
    """
    if abs(gain - 1.0) < 1e-6:
        return samples

    out = samples.astype(np.float32) * float(gain)

    # 峰值已在安全范围内则直接返回
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak <= 0.99:
        return out

    # tanh 软限幅：保证 |out| < 1
    # tanh 在 [-3, 3] 内近似线性，用 3.0 作缩放因子可保留大部分动态
    shaped = np.tanh(out * 1.5) / np.tanh(1.5)
    return np.clip(shaped, -0.99, 0.99).astype(np.float32)


__all__ = [
    "CLAUSE_SEP",
    "CROSSFADE_MS",
    "MAX_SEGMENT_CHARS",
    "MIN_SEGMENT_CHARS",
    "SENTENCE_END",
    "Segment",
    "apply_gain",
    "build_segments",
    "concat_with_pauses",
    "crossfade_concat",
    "split_text",
]
