#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""CLI 退出码契约的测试（通过 subprocess 跑真实命令）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_cli_exit_code.py

【测什么】
`__main__.py` 原来这样写：

    if len(sys.argv) > 1:
        main_cli()        # ← 返回值被丢掉了

而 `main_cli()` 里有多处 `return 1`。返回值丢掉后，
**所有失败路径的退出码都是 0** —— 脚本在 CI / shell 脚本里
无法通过 `$?` 判断成败，只能去 grep 输出文本。

【为什么断言写成「与输出符号一致」而不是「必须非 0」】
`--check-cookie` 的结果取决于本机 Cookie 状态与网络：
- CI 上 cookie 文件不存在 → 走「文件不存在」分支，**不联网**、稳定失败
- 本机可能恰好有有效 cookie → 成功
所以断言「✓ → 0，✗ → 非 0」这个**不变式**，
无论环境如何都成立，不会变成 flaky 测试。
"""

import os
import subprocess
import sys

ZJY5 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


def _run(args):
    return subprocess.run(
        [sys.executable, "-m", "bilibili_downloader"] + args,
        cwd=ZJY5, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
    )


def test_version_exits_zero():
    """--version 应该成功退出。"""
    p = _run(["--version"])
    eq(p.returncode, 0, "--version 退出码")


def test_help_exits_zero():
    """--help 由 argparse 处理，退出码 0。"""
    p = _run(["--help"])
    eq(p.returncode, 0, "--help 退出码")


def test_check_cookie_exit_code_matches_result():
    """核心不变式：✗ 必须非 0，✓ 必须 0。

    ⚠️ 这锁住的正是那个 bug —— 修之前，
    即使输出 `✗ Cookie 已失效`，退出码也是 0。
    """
    p = _run(["--check-cookie"])
    out = p.stdout or ""
    if "✓" in out:
        eq(p.returncode, 0, "检查成功时退出码应为 0")
    elif "✗" in out:
        ok(p.returncode != 0,
           f"检查失败时退出码应非 0，实际 {p.returncode}（输出：{out.strip()[:80]}）")
    else:
        raise Missing(f"没看到成功/失败标记，输出：{out.strip()[:200]}")


def test_check_cookie_does_not_print_traceback():
    """失败也要给可读提示，不能抛栈。"""
    p = _run(["--check-cookie"])
    combined = (p.stdout or "") + (p.stderr or "")
    ok("Traceback" not in combined, f"不该抛异常栈：{combined[:200]}")


def test_module_entry_propagates_exit_code():
    """源码级断言：`__main__.py` 必须把 `main_cli()` 的返回值传给 `sys.exit`。

    加这条是因为上面的行为测试**依赖 `--check-cookie` 的输出**；
    若将来有人改动 `--check-cookie` 的措辞，这条能独立守住入口契约。
    """
    path = os.path.join(ZJY5, "bilibili_downloader", "__main__.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    ok("sys.exit(" in src,
       "__main__.py 应使用 sys.exit(...) 传播退出码，而不是裸调用 main_cli()")


TESTS = [
    test_version_exits_zero,
    test_help_exits_zero,
    test_check_cookie_exit_code_matches_result,
    test_check_cookie_does_not_print_traceback,
    test_module_entry_propagates_exit_code,
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
    print(f"\ntest_cli_exit_code：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
