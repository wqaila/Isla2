"""文本后处理测试：软截断与可朗读化。"""

from __future__ import annotations

import pytest

from server.dialogue.text_utils import ensure_speakable, soft_truncate, split_sentences


def test_split_basic() -> None:
    assert split_sentences("你好。今天不错！走吧？") == ["你好。", "今天不错！", "走吧？"]


def test_split_keeps_newlines_as_boundary() -> None:
    parts = split_sentences("第一段\n第二段")
    assert parts == ["第一段", "第二段"]


def test_split_empty() -> None:
    assert split_sentences("") == []
    assert split_sentences("   ") == []


def test_soft_truncate_by_sentence_count() -> None:
    text = "第一句。第二句。第三句。第四句。"
    out, truncated = soft_truncate(text, max_sentences=2)
    assert out == "第一句。第二句。"
    assert truncated is True


def test_soft_truncate_no_change_when_short() -> None:
    text = "只有一句。"
    out, truncated = soft_truncate(text, max_sentences=5)
    assert out == "只有一句。"
    assert truncated is False


def test_soft_truncate_removes_dangling_comma() -> None:
    """真实场景：模型在「公元前222年，秦灭」处被截断。"""
    text = "杭州历史悠久，分为五个时期。第一个是秦朝，公元前222年，秦灭"
    out, _ = soft_truncate(text, max_sentences=2)
    assert out == "杭州历史悠久，分为五个时期。第一个是秦朝，公元前222年，秦灭"


def test_soft_truncate_trailing_comma_stripped() -> None:
    out, _ = soft_truncate("你好，", max_sentences=5)
    assert not out.endswith("，")


def test_soft_truncate_by_chars() -> None:
    text = "第一句话比较长。第二句话也不短。"
    out, truncated = soft_truncate(text, max_sentences=10, max_chars=8)
    assert truncated is True
    assert out.startswith("第一句")


def test_soft_truncate_never_returns_empty_for_nonempty_input() -> None:
    out, _ = soft_truncate("很短。", max_sentences=1, max_chars=1)
    assert out != ""


def test_soft_truncate_empty_input() -> None:
    assert soft_truncate("") == ("", False)


def test_ensure_speakable_strips_markdown() -> None:
    raw = "**重点**：请看 `代码` 部分。\n\n- 第一项\n- 第二项"
    out = ensure_speakable(raw)
    assert "**" not in out
    assert "`" not in out
    assert "重点" in out


def test_ensure_speakable_removes_code_fence() -> None:
    raw = "前面的话。\n```python\nprint(1)\n```\n后面的话。"
    out = ensure_speakable(raw)
    assert "print(1)" not in out
    assert "前面的话" in out


def test_ensure_speakable_keeps_plain_text() -> None:
    assert ensure_speakable("你好，今天怎么样？") == "你好，今天怎么样？"
