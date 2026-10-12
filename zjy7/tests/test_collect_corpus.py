#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""collect_corpus.py 的测试（纯标准库，不需要 torch / transformers）。

跑法::

    cd zjy7
    python tests/test_collect_corpus.py

覆盖：单文件模式、**目录模式**（接 harvest 的 `downloads/subtitles/` 产物）、
源文件定位、术语校正、空文件拦截。
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collect_corpus import (  # noqa: E402
    collect_from_dir,
    find_src,
    read_lines,
)


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


def _tmpdir():
    return Path(tempfile.mkdtemp(prefix="zjy7_test_"))


def test_read_lines_basic():
    d = _tmpdir()
    p = d / "a.txt"
    p.write_text("你好呀\n\n  第二句  \n", encoding="utf-8")
    lines, warns = read_lines(p)
    eq(lines, ["你好呀", "第二句"], "去空行与首尾空白")
    eq([w for w in warns if "空" in w], [], "有内容时不该报空文件")


def test_read_lines_empty_warns():
    d = _tmpdir()
    p = d / "empty.txt"
    p.write_text("\n\n   \n", encoding="utf-8")
    lines, warns = read_lines(p)
    eq(lines, [], "空文件没有有效行")
    ok(any("一个有效行都没有" in w for w in warns), "空文件必须告警")


def test_read_lines_applies_term_fix():
    """read_lines 应调用 zjy5 的 term_fix 修 OCR 错字（跨项目复用）。"""
    d = _tmpdir()
    p = d / "b.txt"
    p.write_text("牙医，你回来了\n我就算变成了子子的样子\n", encoding="utf-8")
    lines, warns = read_lines(p)
    eq(lines, ["芽衣，你回来了", "我就算变成了紫紫的样子"], "OCR 术语应被校正")
    ok(any("术语校正" in w for w in warns), "应报告校正了几行")


def test_collect_from_dir_merges_sorted():
    """目录模式：把 harvest 产出的多个 txt 合并，**按文件名排序**保证可复现。"""
    d = _tmpdir()
    (d / "BV1c.txt").write_text("第三句\n", encoding="utf-8")
    (d / "BV1a.txt").write_text("第一句\n", encoding="utf-8")
    (d / "BV1b.txt").write_text("第二句\n", encoding="utf-8")
    lines, warns, files = collect_from_dir(d)
    eq(lines, ["第一句", "第二句", "第三句"], "按文件名排序合并")
    eq(len(files), 3, "应处理 3 个文件")


def test_collect_from_dir_skips_non_txt_and_subdirs():
    d = _tmpdir()
    (d / "ok.txt").write_text("有效\n", encoding="utf-8")
    (d / "note.md").write_text("不该被读\n", encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / "nested.txt").write_text("不该被读\n", encoding="utf-8")
    lines, _warns, files = collect_from_dir(d)
    eq(lines, ["有效"], "只读顶层 .txt")
    eq(len(files), 1, "只处理 1 个文件")


def test_collect_from_dir_handles_empty_dir():
    d = _tmpdir()
    lines, warns, files = collect_from_dir(d)
    eq(lines, [], "空目录无内容")
    eq(files, [], "空目录无文件")
    ok(any("没有" in w or "找不到" in w for w in warns), "空目录应告警")


def test_collect_from_dir_reports_per_file():
    """合并时要保留「每个文件各贡献多少行」的信息，便于回溯来源。"""
    d = _tmpdir()
    (d / "a.txt").write_text("一\n二\n", encoding="utf-8")
    (d / "b.txt").write_text("三\n", encoding="utf-8")
    lines, _warns, files = collect_from_dir(d)
    counts = {f.name: f.line_count for f in files}
    eq(counts, {"a.txt": 2, "b.txt": 1}, "每个文件的行数")
    eq(len(lines), 3, "总行数")


def test_find_src_dir_mode():
    """`--src` 传目录时应能识别，而不是当成不存在的文件。"""
    d = _tmpdir()
    (d / "x.txt").write_text("台词\n", encoding="utf-8")
    got = find_src(str(d))
    eq(got, d, "目录应被原样返回")


def test_find_src_missing():
    eq(find_src(str(_tmpdir() / "nope.txt")), None, "不存在的路径返回 None")


TESTS = [
    test_read_lines_basic,
    test_read_lines_empty_warns,
    test_read_lines_applies_term_fix,
    test_collect_from_dir_merges_sorted,
    test_collect_from_dir_skips_non_txt_and_subdirs,
    test_collect_from_dir_handles_empty_dir,
    test_collect_from_dir_reports_per_file,
    test_find_src_dir_mode,
    test_find_src_missing,
]


def run():
    passed = 0
    failed = 0
    for test in TESTS:
        try:
            test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except Exception as e:                      # 打印出来，别让失败悄悄过去
            print(f"  ✗ {test.__name__}：{type(e).__name__}: {e}")
            failed += 1
    print(f"\ntest_collect_corpus：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
