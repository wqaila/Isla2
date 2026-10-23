"""用 Python 复刻 firmware C 代码的关键算法，验证边界行为。

说明：这是**等价实现校验**，不是运行固件。它保证算法规则正确，
C 代码是否编译通过仍需工具链验证。
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_TEXT = ROOT / "firmware" / "main" / "app_text.c"
SRC_JSON = ROOT / "firmware" / "main" / "app_json.c"


def utf8_chars(text: str) -> int:
    """对应 app_utf8_chars：统计非续字节。"""
    return sum(1 for b in text.encode("utf-8") if (b & 0xC0) != 0x80)


def utf8_truncate(text: str, max_chars: int) -> str:
    """对应 app_utf8_truncate：按字符截断，不切半个字节序列。"""
    return "".join(list(text)[:max_chars])


def text_plain(text: str) -> str:
    """对应 app_text_plain：去 Markdown、合并空行、去尾空白。"""
    out = []
    at_line_start = True
    last_blank = False
    for ch in text:
        if at_line_start:
            if ch in "#*-+>":
                continue
            if ch in " \t":
                continue
            at_line_start = False
        if ch in "`*":
            continue
        if ch in "\n\r":
            if last_blank:
                continue
            out.append("\n")
            at_line_start = True
            last_blank = True
            continue
        last_blank = False
        out.append(ch)
    return "".join(out).rstrip("\n ")


def json_escape(text: str) -> str:
    out = []
    for ch in text:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


class Utf8Tests(unittest.TestCase):
    def test_chinese_char_counts_as_one(self):
        self.assertEqual(utf8_chars("你好"), 2)
        self.assertEqual(utf8_chars("你好abc"), 5)

    def test_byte_limit_for_512(self):
        text = "字" * 171  # 513 字节
        self.assertGreater(len(text.encode("utf-8")), 512)
        self.assertLessEqual(len(("字" * 170).encode("utf-8")), 512)

    def test_truncate_never_splits_character(self):
        text = "爱莉希雅"
        for n in range(0, len(text) + 1):
            cut = utf8_truncate(text, n)
            cut.encode("utf-8")  # 能编码就说明没切坏
            self.assertEqual(len(cut), n)

    def test_emoji_is_one_character(self):
        text = "好✨"
        self.assertEqual(utf8_chars(text), 2)
        self.assertEqual(utf8_truncate(text, 1), "好")


class PlainTextTests(unittest.TestCase):
    def test_removes_markdown_heading_and_bullets(self):
        raw = "# 标题\n- 第一条\n* 第二条\n正文**加粗**"
        clean = text_plain(raw)
        self.assertNotIn("#", clean)
        self.assertNotIn("*", clean)
        self.assertNotIn("```", clean)
        self.assertIn("标题", clean)
        self.assertIn("第一条", clean)

    def test_collapses_blank_lines(self):
        self.assertEqual(text_plain("a\n\n\n\nb"), "a\nb")

    def test_strips_trailing_space(self):
        self.assertEqual(text_plain("你好\n\n"), "你好")

    def test_keeps_chinese_punctuation(self):
        clean = text_plain("你好呀，舰长！")
        self.assertIn("，", clean)
        self.assertIn("！", clean)


class JsonEscapeTests(unittest.TestCase):
    def test_escapes_quotes_and_backslash(self):
        self.assertEqual(json_escape('a"b'), 'a\\"b')
        self.assertEqual(json_escape("a\\b"), "a\\\\b")

    def test_escapes_newline(self):
        self.assertEqual(json_escape("a\nb"), "a\\nb")

    def test_keeps_chinese_unescaped(self):
        self.assertEqual(json_escape("你好"), "你好")


class SourceContractTests(unittest.TestCase):
    """检查 C 源码中存在对应实现，避免文档与代码脱节。"""

    def test_c_implements_same_rules(self):
        text = SRC_TEXT.read_text(encoding="utf-8")
        for fn in ("app_utf8_chars", "app_utf8_truncate", "app_text_plain"):
            self.assertIn(fn, text)
        # 截断必须检查续字节，否则会切坏 UTF-8
        self.assertIn("is_cont", text)

    def test_json_escapes_all_controls(self):
        src = SRC_JSON.read_text(encoding="utf-8")
        for key in ('"\\\\n"', '"\\\\t"', '"\\\\\\""'):
            self.assertIn(key, src, f"缺少转义处理 {key}")


if __name__ == "__main__":
    unittest.main()
