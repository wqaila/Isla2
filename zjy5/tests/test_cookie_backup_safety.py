#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Cookie 备份文件的**防泄露**测试（不联网）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_cookie_backup_safety.py

【要解决的问题（2026-10-15 实测发现）】
GUI 的「保存前备份」会生成：

    bilibili_cookies.txt.bak-20261015-145237

⚠️ 备份里是**同样完整的登录凭据**，但两处防护都没覆盖它：

1. `bilibili_downloader/.gitignore` 与根 `.gitignore` 里写的是
   **精确文件名** `bilibili_cookies.txt` —— 备份名不匹配，真实 git **不会忽略**；
2. 推送收集器 `collect_files.py` 的 `SKIP_NAMES` 也是精确匹配。
   实测它恰好被 `".bak" in fn` 这条**文件名包含判断**挡住 ——
   **纯属巧合，不是设计**（换个不带 .bak 的备份名就会漏）。

一旦备份被推送进 public 仓库，就等于把凭据公开了。
所以这里把「备份必须被忽略」固化成测试。
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ZJY5 = os.path.dirname(HERE)
TOOLS = os.path.join(os.path.dirname(ZJY5), ".workbuddy-ai", "tools")

sys.path.insert(0, TOOLS)


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


BACKUP_NAME = "bilibili_cookies.txt.bak-20261015-145237"
COOKIE_DIR = os.path.join(ZJY5, "bilibili_downloader")


def test_push_collector_skips_backup_by_design():
    """推送收集器必须**真的不收**凭据备份，且不依赖 `.bak` 这条通用规则。

    ⚠️ 用**行为断言**而不是检查规则常量 —— 第一版断言
    「备份名命中 SKIP_NAMES/SKIP_EXTS/前缀之一」，但那只是**规则配置**；
    把前缀规则整条删掉后，断言仍可能因 `.bak` 判断而通过（实测确实如此）。
    现在改为：**真的跑一遍收集器**，确认备份名不在结果里。
    """
    from collect_files import collect

    files = {r for r, _ in collect()[0]}
    leaked = [f for f in files if "bilibili_cookie" in f.lower()]
    eq(leaked, [],
       f"收集器不该收任何 cookie 相关文件，实际收到：{leaked}")


def test_prefix_rule_is_what_blocks_non_bak_names():
    """不带 `.bak` 的凭据备份名也必须**真的被收集器跳过**。

    ⚠️ 关键对照实验：`bilibili_cookies.old-2026` **不含 `.bak`**，
    所以只能靠前缀规则挡住。

    ⚠️ 必须**端到端验证**：第一版只断言「常量 SKIP_PREFIXES 含这些前缀」，
    但那只检查**配置**，不检查**是否被使用** —— 把使用它的那行 `if` 删掉后
    断言仍通过（实测确实如此）。现在**真造一个文件**放进目录跑收集器。
    """
    from collect_files import collect

    # 造一个真实的不带 .bak 的凭据备份文件
    fake_dir = os.path.join(ZJY5, "bilibili_downloader")
    fake = os.path.join(fake_dir, "bilibili_cookies.old-20260999")
    created = False
    try:
        with open(fake, "w", encoding="utf-8") as f:
            f.write("SESSDATA=fake-for-test\n")
        created = True
        files = {r for r, _ in collect()[0]}
        ok("zjy5/bilibili_downloader/bilibili_cookies.old-20260999" not in files,
           "不带 .bak 的凭据备份也必须被收集器跳过（只能靠前缀规则）")
    finally:
        if created and os.path.exists(fake):
            os.remove(fake)


def test_gitignore_covers_backup():
    """两个 `.gitignore` 都必须忽略 `bilibili_cookies.txt.bak-*`。

    ⚠️ 用 `git check-ignore` **实测**，不看文件里写了什么 ——
    写了规则但不生效（路径写法错、被后面的 `!` 覆盖）也是白搭。
    """
    target = f"bilibili_downloader/{BACKUP_NAME}"
    p = subprocess.run(["git", "check-ignore", "-v", target],
                       cwd=ZJY5, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    eq(p.returncode, 0,
       f"git 应忽略 {target}；check-ignore 返回 {p.returncode}、输出 {p.stdout!r}")


def test_gitignore_still_covers_original():
    """原文件当然也要继续被忽略（别改出回归）。"""
    p = subprocess.run(["git", "check-ignore", "-v",
                        "bilibili_downloader/bilibili_cookies.txt"],
                       cwd=ZJY5, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    eq(p.returncode, 0, "原 Cookie 文件必须仍被忽略")


def test_no_urecognized_credential_file_names():
    """换几个常见备份命名，都该被忽略（防「只挡了一种」）。"""
    for name in ("bilibili_cookies.txt.bak-20260101-000000",
                 "bilibili_cookies.txt.bak-1",
                 "bilibili_cookies.bak"):
        target = f"bilibili_downloader/{name}"
        p = subprocess.run(["git", "check-ignore", "-q", target],
                           cwd=ZJY5, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        eq(p.returncode, 0, f"{name} 应被忽略（否则换命名就泄露）")


def test_gui_backup_path_is_under_cookie_dir():
    """GUI 生成的备份必须落在被忽略的目录里（而不是项目根/临时目录）。"""
    sys.path.insert(0, COOKIE_DIR)
    from cookie_gui import CookieEditor
    e = CookieEditor()
    # 备份路径由 cookie_path 派生 —— 确认它与 cookie 文件同目录
    eq(os.path.dirname(e.cookie_path), COOKIE_DIR,
       "cookie 文件应在 bilibili_downloader/ 下（该目录有 .gitignore）")


TESTS = [
    test_push_collector_skips_backup_by_design,
    test_prefix_rule_is_what_blocks_non_bak_names,
    test_gitignore_covers_backup,
    test_gitignore_still_covers_original,
    test_no_urecognized_credential_file_names,
    test_gui_backup_path_is_under_cookie_dir,
]


def run():
    passed = 0
    failed = 0
    for test in TESTS:
        try:
            test()
            print(f"  ✓ {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__}：{type(e).__name__}: {e}")
            failed += 1
    print(f"\ntest_cookie_backup_safety：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
