"""文本后处理：软截断与可朗读化。

背景（实测）：云端返回 ``finish_reason="length"`` 时，文本会在半个句子处被切断，
例如「公元前222年，秦灭」。这种半句话送去 TTS 会念出残句，体验很差。

本模块在**句边界**收尾，宁可少说一句，也不留半句。
"""

from __future__ import annotations

import re

# 句末标点（中英文）
_SENTENCE_END = re.compile(r"[。！？!?；;…]+|\n+")
# 这些结尾不算完整句子，需要继续找
_TRAILING_INCOMPLETE = re.compile(r"[，,、：:（(【\[]+$")

DEFAULT_MAX_SENTENCES = 5


def split_sentences(text: str) -> list[str]:
    """按句末标点切分，保留标点。空串返回空列表。"""
    text = (text or "").strip()
    if not text:
        return []
    parts: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        end = match.end()
        piece = text[start:end]
        if piece.strip():
            parts.append(piece.strip())
        start = end
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def soft_truncate(
    text: str,
    *,
    max_sentences: int = DEFAULT_MAX_SENTENCES,
    max_chars: int | None = None,
) -> tuple[str, bool]:
    """在句边界截断文本。

    :return: (处理后文本, 是否发生了截断)
    """
    text = (text or "").strip()
    if not text:
        return "", False

    sentences = split_sentences(text)
    truncated = False

    if max_sentences > 0 and len(sentences) > max_sentences:
        sentences = sentences[:max_sentences]
        truncated = True

    if max_chars is not None and max_chars > 0:
        out: list[str] = []
        used = 0
        for s in sentences:
            if used + len(s) > max_chars:
                truncated = True
                break
            out.append(s)
            used += len(s)
        # 至少保留一句，避免 max_chars 过小导致完全空输出
        sentences = out or sentences[:1]

    result = "".join(sentences).strip()

    # 若结尾是逗号/顿号等未完成符号，去掉它，避免"念到一半"
    result = _TRAILING_INCOMPLETE.sub("", result).strip()

    if not result:
        return "", True
    if result != text:
        truncated = True
    return result, truncated


def ensure_speakable(text: str) -> str:
    """把不适合朗读的符号清理掉（供 TTS 阶段使用）。

    注意：本阶段（LLM）不改变返回给屏幕显示的文本，只在送 TTS 前调用。
    """
    text = (text or "").strip()
    if not text:
        return ""
    # Markdown 强调符号、代码围栏、列表符号
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
    text = re.sub(r"^\s*[-+*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*#{1,6}\s+", "", text, flags=re.MULTILINE)
    # 连续空白压成单个空格
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


__all__ = [
    "split_sentences",
    "soft_truncate",
    "ensure_speakable",
    "DEFAULT_MAX_SENTENCES",
]
