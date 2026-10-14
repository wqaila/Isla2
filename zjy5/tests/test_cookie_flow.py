#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Cookie 获取路径的测试（纯标准库，不联网、不弹浏览器）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_cookie_flow.py

【背景：两个真实缺陷挡住了「更新 Cookie」这件事】
1. **交互模式「查看 Cookie 状态」误报有效**：它用的是 `is_logged_in()`，
   而那个只查**文件里有没有 `SESSDATA` 字符串**。Cookie 过期后它照样返回
   True → 用户看到「Cookie 已设置且有效」，完全不知道已经失效。
   实测：`is_logged_in()` 返回 True，而服务端返回 `-101 账号未登录`。
2. **自动获取从未被调用**：`get_cookies_from_playwright()` 只被定义、
   全项目**没有任何调用点** → 「自动获取 Cookie」形同虚设。

【本次决策：屏蔽自动获取，保留手动粘贴】
Playwright 自动获取需要**弹出可见浏览器窗口让用户手动登录**，
在无 GUI / 沙箱环境里根本走不通（本机也没装 Playwright 浏览器）。
所以按用户要求把它屏蔽掉，只保留「手动粘贴 Cookie 字符串」这条路。
"""

import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "bilibili_downloader"))
# main.py 需要能 import（它把 cv2/paddle 等重依赖都放在 try 里，所以可导入）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir))

from cookie_manager import CookieManager  # noqa: E402


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


def _write_cookie(dir_path, names=("SESSDATA", "bili_jct", "DedeUserID")):
    p = os.path.join(dir_path, "cookies.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(COOKIE_HEAD)
        for n in names:
            f.write(f".bilibili.com\tTRUE\t/\tTRUE\t0\t{n}\tval_{n}\n")
    return p


# ---------------------------------------------------------------- 手动粘贴

def test_set_cookie_from_string():
    """手动粘贴 Cookie 字符串应能落盘成 Netscape 格式。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ck.txt")
        cm = CookieManager(cookie_path=path)
        cm.set_cookie("SESSDATA=abc123; bili_jct=def456; DedeUserID=789")

        ok(os.path.exists(path), "应写出 Cookie 文件")
        d = cm.get_cookie_dict()
        eq(d.get("SESSDATA"), "abc123", "SESSDATA 落盘")
        eq(d.get("bili_jct"), "def456", "bili_jct 落盘")
        eq(d.get("DedeUserID"), "789", "DedeUserID 落盘")


def test_set_cookie_rejects_empty():
    """空字符串不该写文件（避免把有效 Cookie 清掉）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_cookie(tmp)
        before = open(path, encoding="utf-8").read()
        cm = CookieManager(cookie_path=path)
        cm.set_cookie("")
        eq(open(path, encoding="utf-8").read(), before, "空输入不该改动文件")


def test_set_cookie_preserves_raw_value():
    """Cookie 值里可能含 `=`（如 base64），不能按第一个 = 截断。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ck.txt")
        cm = CookieManager(cookie_path=path)
        cm.set_cookie("SESSDATA=a=b=c%2Fd")
        eq(cm.get_cookie_dict().get("SESSDATA"), "a=b=c%2Fd", "应保留完整值")


# ---------------------------------------------------------------- 状态显示

def test_status_check_prefers_online():
    """「选 2 查看 Cookie 状态」必须**真的调用**联网检查。

    ⚠️ 这是核心回归：原来用 `is_logged_in()`，Cookie 过期时照样报「有效」，
    用户粘完新 Cookie 也看不出有没有生效。

    ⚠️ 用**行为断言**而非源码搜索 —— 第一版在 `configure_settings` 里搜
    `check_cookie_online`，但那个字符串在该函数里出现 **2 次**
    （「选 1 粘贴后校验」+「选 2 查看状态」），
    所以把「选 2」退回旧写法时**断言仍然成立** → 变异抓不到。
    现在直接跑一遍「选 2」并观察是否调用了联网检查。
    """
    import io
    import main as cli_mod
    from contextlib import redirect_stdout
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmp:
        cm = CookieManager(cookie_path=_write_cookie(tmp))
        called = {"n": 0}

        def fake_check(*a, **kw):
            called["n"] += 1
            return False, "Cookie 已失效 / 未登录（模拟）"

        fake_in = iter(["2"])
        buf = io.StringIO()
        with mock.patch("builtins.input", lambda *a: next(fake_in)), \
             mock.patch.object(cli_mod, "CookieManager", return_value=cm), \
             mock.patch.object(cm, "check_cookie_online", side_effect=fake_check):
            with redirect_stdout(buf):
                cli_mod.configure_settings()

        eq(called["n"], 1, "选 2 必须调用一次联网检查（不能只用 is_logged_in）")
        out = buf.getvalue()
        ok("✗" in out or "✓" in out, f"应输出校验结果，实际：{out[:100]}")


def test_status_offline_mode_still_works():
    """应保留一个不联网的口子（无网络时也能看本地状态）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_cookie(tmp)
        cm = CookieManager(cookie_path=path)
        ok(cm.is_logged_in(), "本地判断仍可用（不该被删掉）")
        ok(hasattr(cm, "check_cookie_online"), "联网检查也应保留")


# ---------------------------------------------------------------- 屏蔽自动获取

def test_playwright_autofetch_disabled():
    """自动获取 Cookie 应被**屏蔽**：不弹浏览器、直接给出「请手动粘贴」指引。

    ⚠️ 为什么不删掉方法：保留签名让想启用的人一眼看到原实现，
    也避免外部代码因 AttributeError 直接崩。
    但**行为上必须是不弹浏览器、导向手动粘贴**。

    ⚠️ 这条测试不能只靠「playwright 没装所以失败」来通过 ——
    那在装了 playwright 的机器上就会误判。所以同时断言**源码里
    不再出现真正启动浏览器的调用**。
    """
    src = inspect.getsource(CookieManager.get_cookies_from_playwright)
    ok("sync_playwright()" not in src,
       "屏蔽后源码里不该再有启动浏览器的调用")

    with tempfile.TemporaryDirectory() as tmp:
        cm = CookieManager(cookie_path=os.path.join(tmp, "ck.txt"))
        result = cm.get_cookies_from_playwright()
        eq(result, False, "屏蔽后应返回 False（未获取到）")


def test_autofetch_hints_manual_paste():
    """屏蔽后的提示必须**告诉用户怎么办**（否则只看到「失败」会以为坏了）。

    ⚠️ 用**行为断言**（捕获输出）而不是搜源码 —— 第一版只搜源码里有没有
    「手动」，但改掉具体那行提示后，docstring 里仍有「手动粘贴」字样，
    断言照样成立 → 变异抓不到。
    """
    import io
    from contextlib import redirect_stdout

    with tempfile.TemporaryDirectory() as tmp:
        cm = CookieManager(cookie_path=os.path.join(tmp, "ck.txt"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            cm.get_cookies_from_playwright()
        out = buf.getvalue()

    ok("手动" in out or "粘贴" in out, f"应提示改用手动粘贴，实际输出：{out[:150]}")
    ok("已屏蔽" in out or "不可用" in out or "已关闭" in out,
       f"应说明该功能不可用，实际输出：{out[:150]}")


TESTS = [
    test_set_cookie_from_string,
    test_set_cookie_rejects_empty,
    test_set_cookie_preserves_raw_value,
    test_status_check_prefers_online,
    test_status_offline_mode_still_works,
    test_playwright_autofetch_disabled,
    test_autofetch_hints_manual_paste,
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
    print(f"\ntest_cookie_flow：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
