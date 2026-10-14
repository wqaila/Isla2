#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""`--retry-downloads` 的端到端测试（不联网：用假 downloader 与临时清单）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_retry_downloads.py

【为什么单独测它】
`--retry-downloads` 要能**真的跑起来**才算数。它有两个容易踩的坑：

1. **被交互模式拦截** —— `main_cli` 里有个「没给任务参数就进交互模式」的
   判断，如果 `--retry-downloads` 不在那个列表里，用户敲了参数却会掉进
   交互式问答（明明给了参数，却被问「请输入 URL」）。
2. **空清单要早退** —— 清单为空时应提示「没有需要重试的」并成功退出，
   而不是当作「没有任务」掉进交互模式。

⚠️ 这里**不联网**：清单文件是本地造的，重试用假的 downloader。
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ZJY5 = os.path.dirname(HERE)

sys.path.insert(0, os.path.join(ZJY5, "bilibili_downloader"))

from download_failures import FailureStore  # noqa: E402


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")

def ok(condition, label):
    if not condition:
        raise Missing(label)


def _cli_source():
    path = os.path.join(ZJY5, "bilibili_downloader", "main.py")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_retry_downloads_not_swallowed_by_interactive():
    """源码级断言：`--retry-downloads` 必须在「有任务参数」的判断里。

    ⚠️ 这是回归测试。改之前 `--retry-downloads` 不在列表里，
    敲了它会被判定成「没给任务」→ 掉进交互式问答。
    """
    src = _cli_source()
    idx = src.find("parsed_args.interactive or not (")
    ok(idx != -1, "应能找到交互模式判断")
    seg = src[idx:idx + 400]
    ok("retry_downloads" in seg,
       "`--retry-downloads` 必须出现在交互模式判断里，否则会被拦截")


def test_failure_list_created_and_readable():
    """失败清单能被 CLI 生成并读回（用真实 FailureStore 写，再读）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "failed.json")
        s = FailureStore(path)
        s.record("https://www.bilibili.com/video/BV1xx411c7mD", reason="返回码 1")
        s.save()

        back = FailureStore(path)
        eq(len(back), 1, "清单应有 1 条")
        eq(back.urls()[0], "https://www.bilibili.com/video/BV1xx411c7mD", "URL 一致")


def test_empty_failure_list_exits_cleanly():
    """空清单时 `--retry-downloads` 应友好退出（退出码 0），而不是报错。

    ⚠️ 用真实的 CLI 子进程跑。因为清单为空时它**不联网**
    （连 URL 都没有，无从请求），所以在 CI 上稳定可跑。
    """
    with tempfile.TemporaryDirectory() as tmp:
        empty = os.path.join(tmp, "empty.json")
        with open(empty, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "records": []}, f)

        p = subprocess.run(
            [sys.executable, "-m", "bilibili_downloader",
             "--retry-downloads", "--failure-list", empty, "--output", tmp],
            cwd=ZJY5, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        out = (p.stdout or "") + (p.stderr or "")
        eq(p.returncode, 0, f"空清单应正常退出，实际 {p.returncode}；输出：{out[:200]}")
        ok("没有需要重试" in out or "是空的" in out,
           f"应提示清单为空，实际输出：{out[:200]}")
        # 关键：不能掉进交互式问答
        ok("请输入" not in out, f"不该进入交互模式，实际输出：{out[:200]}")


def test_missing_failure_list_treated_as_empty():
    """清单文件不存在也应友好退出（首次运行就是这种情况）。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run(
            [sys.executable, "-m", "bilibili_downloader",
             "--retry-downloads", "--failure-list", os.path.join(tmp, "nope.json"),
             "--output", tmp],
            cwd=ZJY5, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        out = (p.stdout or "") + (p.stderr or "")
        eq(p.returncode, 0, f"文件不存在应正常退出，实际 {p.returncode}")
        ok("请输入" not in out, "不该进入交互模式")


TESTS = [
    test_retry_downloads_not_swallowed_by_interactive,
    test_failure_list_created_and_readable,
    test_empty_failure_list_exits_cleanly,
    test_missing_failure_list_treated_as_empty,
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
    print(f"\ntest_retry_downloads：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
