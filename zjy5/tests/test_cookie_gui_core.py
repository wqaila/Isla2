#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Cookie 设置界面的**核心逻辑**测试（不依赖 tkinter，不弹窗、不联网）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_cookie_gui_core.py

【为什么把逻辑与界面分开】
界面代码（tkinter）没法在 CI 上跑，也不适合写断言。所以把
「保存前备份」「校验」「状态描述」这些**真正会出错的逻辑**
抽到一个不依赖 tkinter 的类里，单独测。

界面层只负责：读输入框 → 调这里 → 把返回值显示出来。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "bilibili_downloader"))

from cookie_gui import CookieEditor  # noqa: E402


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


COOKIE_HEAD = ("# Netscape HTTP Cookie File\n"
               "# https://curl.haxx.se/docs/http-cookies.html\n\n")
GOOD = "SESSDATA=abc123; bili_jct=def456; DedeUserID=789"


def _editor(tmp, online=None):
    """造一个把联网校验替换成假的 editor（不真发请求）。"""
    e = CookieEditor(cookie_path=os.path.join(tmp, "ck.txt"))
    if online is not None:
        e._check_online = lambda: online
    return e


# ---------------------------------------------------------------- 保存前备份

def test_save_backs_up_existing_file():
    """保存前必须**备份**旧文件 —— 否则手滑粘贴就永久丢了原 Cookie。

    ⚠️ `CookieManager.set_cookie()` 是**直接覆盖**的，没有备份。
    界面里粘贴很容易出错（粘错内容、粘成半截），没有退路会很痛。
    """
    with tempfile.TemporaryDirectory() as tmp:
        e = _editor(tmp, online=(True, "✓ 有效"))
        e.save(GOOD)
        # 改一次，触发备份
        e.save("SESSDATA=new; bili_jct=x; DedeUserID=1")
        backups = [f for f in os.listdir(tmp) if ".bak" in f]
        ok(backups, f"应生成备份文件，实际目录：{os.listdir(tmp)}")


def test_no_backup_on_first_save():
    """首次保存（原本没有文件）不该产生无意义的备份。"""
    with tempfile.TemporaryDirectory() as tmp:
        e = _editor(tmp, online=(True, "✓ 有效"))
        e.save(GOOD)
        backups = [f for f in os.listdir(tmp) if ".bak" in f]
        eq(backups, [], "首次保存不该有备份（没有旧文件可备）")


# ---------------------------------------------------------------- 保存与校验

def test_save_rejects_empty():
    """空输入应被拒，且**不动**已有文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        e = _editor(tmp, online=(True, "✓ 有效"))
        e.save(GOOD)
        before = open(e.cookie_path, encoding="utf-8").read()
        ok_flag, msg = e.save("   ")
        eq(ok_flag, False, "空输入应返回失败")
        eq(open(e.cookie_path, encoding="utf-8").read(), before, "不该改动文件")


def test_save_warns_when_sessdata_missing():
    """缺 SESSDATA 时应**明确提示**（这是最常见的手滑）。

    ⚠️ 真实案例：用户复制到的是**游客 Cookie**（一堆 buvid3/_uuid，
    没有 SESSDATA），保存后以为成功了，其实根本没登录。
    这种情况必须在保存时就说清，而不是等下载失败。
    """
    with tempfile.TemporaryDirectory() as tmp:
        e = _editor(tmp, online=(False, "✗ Cookie 已失效 / 未登录"))
        ok_flag, msg = e.save("buvid3=xxx; _uuid=yyy; bili_ticket=zzz")
        eq(ok_flag, False, "缺 SESSDATA 应判失败")
        ok("SESSDATA" in msg, f"提示应点名 SESSDATA，实际：{msg}")
        ok("游客" in msg or "未登录" in msg or "登录" in msg,
           f"应说明可能是游客 Cookie，实际：{msg}")


def test_save_returns_online_verification():
    """保存后应返回**联网校验**结果（含用户名），而不是只说「已保存」。"""
    with tempfile.TemporaryDirectory() as tmp:
        e = _editor(tmp, online=(True, "✓ Cookie 有效，当前登录：测试舰长"))
        ok_flag, msg = e.save(GOOD)
        eq(ok_flag, True, "校验通过应返回 True")
        ok("测试舰长" in msg, f"应回显用户名，实际：{msg}")


def test_save_reports_when_verification_fails():
    """保存成功但校验失败时，要如实说「存了但没用」。"""
    with tempfile.TemporaryDirectory() as tmp:
        e = _editor(tmp, online=(False, "✗ Cookie 已失效 / 未登录"))
        ok_flag, msg = e.save(GOOD)      # 有 SESSDATA，但服务端不认
        eq(ok_flag, False, "校验失败应返回 False")
        ok("失效" in msg or "未登录" in msg,
           f"应说明失效，实际：{msg}")


def test_save_strips_whitespace_and_newlines():
    """粘贴的内容常带换行/首尾空白，应自动清理。

    （从浏览器复制时特别容易出现前后换行，或整段黏在一起。）
    """
    with tempfile.TemporaryDirectory() as tmp:
        e = _editor(tmp, online=(True, "✓ 有效"))
        e.save("\n  SESSDATA=abc123;  bili_jct=def456  \n\n")
        d = e.cookies()
        eq(d.get("SESSDATA"), "abc123", "应清理空白后落盘")


def test_save_handles_value_containing_equals():
    """Cookie 值里可能含 `=`（如 base64），不能截断。"""
    with tempfile.TemporaryDirectory() as tmp:
        e = _editor(tmp, online=(True, "✓ 有效"))
        e.save("SESSDATA=a=b=c%2Fd; bili_jct=x")
        eq(e.cookies().get("SESSDATA"), "a=b=c%2Fd", "应保留完整值")


# ---------------------------------------------------------------- 状态与清除

def test_status_reports_online_result():
    """「查看状态」应走联网校验并回显结果。

    ⚠️ 必须先**存在文件** —— 否则会走「文件不存在」分支，
    测不到联网校验那条路径（第一版就踩了这个）。
    """
    with tempfile.TemporaryDirectory() as tmp:
        e = _editor(tmp, online=(True, "✓ Cookie 有效，当前登录：某人"))
        e.save(GOOD)                    # 先让文件存在
        txt = e.status()
        ok("某人" in txt or "有效" in txt, f"应含校验结果，实际：{txt}")


def test_status_when_no_file():
    """没有 Cookie 文件时也要有明确状态，而不是报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        e = _editor(tmp, online=(False, "✗ Cookie 文件不存在"))
        txt = e.status()
        ok(txt, "应返回非空状态文本")


def test_clear_removes_file_and_backs_up():
    """「清除」应删掉文件；若之前有内容，也应留备份。"""
    with tempfile.TemporaryDirectory() as tmp:
        e = _editor(tmp, online=(True, "✓ 有效"))
        e.save(GOOD)
        ok(os.path.exists(e.cookie_path), "先确认文件在")
        txt = e.clear()
        ok(not os.path.exists(e.cookie_path), "清除后文件应不存在")
        ok(txt, "应返回状态文本")
        backups = [f for f in os.listdir(tmp) if ".bak" in f]
        ok(backups, "清除前应留备份（避免误点）")


def test_clear_when_nothing_to_clear():
    """没有文件时清除不该抛异常。"""
    with tempfile.TemporaryDirectory() as tmp:
        e = _editor(tmp, online=(False, "✗ 不存在"))
        txt = e.clear()
        ok(txt, "应返回友好提示")


# ---------------------------------------------------------------- 界面可用性

def test_has_main_entry():
    """模块应提供可启动的入口（供 .bat / 命令行调用）。"""
    import cookie_gui
    ok(callable(getattr(cookie_gui, "main", None)), "应提供 main() 入口")


TESTS = [
    test_save_backs_up_existing_file,
    test_no_backup_on_first_save,
    test_save_rejects_empty,
    test_save_warns_when_sessdata_missing,
    test_save_returns_online_verification,
    test_save_reports_when_verification_fails,
    test_save_strips_whitespace_and_newlines,
    test_save_handles_value_containing_equals,
    test_status_reports_online_result,
    test_status_when_no_file,
    test_clear_removes_file_and_backs_up,
    test_clear_when_nothing_to_clear,
    test_has_main_entry,
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
    print(f"\ntest_cookie_gui_core：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
