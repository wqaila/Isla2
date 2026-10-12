#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Cookie **有效性**检测的测试（纯标准库，不联网：注入假 opener）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_cookie_manager.py

【为什么需要这个功能】
原来的 `is_logged_in()` 只检查**文件里有没有 `SESSDATA` 这个字符串**，
不验证它**还有没有效**。实际后果：

    Cookie 过期后，B 站接口不是报错，而是**静默返回未登录结果** ——
    表现为「字幕全都探测不到」「视频信息取不到」，
    看起来像风控或接口改版，**很难联想到是 Cookie 过期**。

⚠️ 本机踩过同类坑：zjy9 的 `ollama_client` 曾因为没关 `trust_env`，
系统代理把本机请求也劫持了，报错完全看不出原因。
所以这里的 opener 也用 `ProxyHandler({})` 绕开系统代理。
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "bilibili_downloader"))

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
COOKIE_LINE = ".bilibili.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n"


def _write_cookie(dir_path, names=("SESSDATA", "bili_jct")):
    p = Path(dir_path) / "cookies.txt"
    body = "".join(COOKIE_LINE.format(name=n, value=f"val_{n}") for n in names)
    p.write_text(COOKIE_HEAD + body, encoding="utf-8")
    return str(p)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        import json
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """假 opener：记录请求过的 URL，返回预设 payload 或抛异常。"""

    def __init__(self, payload=None, fail=None):
        self.payload = payload
        self.fail = fail
        self.urls = []

    def open(self, url, timeout=None, data=None):
        self.urls.append(url)
        if self.fail:
            raise self.fail
        return FakeResponse(self.payload)


NAV_LOGGED_IN = {
    "code": 0,
    "data": {"isLogin": True, "uname": "测试舰长", "mid": 12345},
}
NAV_NOT_LOGGED_IN = {
    "code": -101,
    "message": "账号未登录",
    "data": {"isLogin": False},
}


def test_missing_cookie_file():
    d = tempfile.mkdtemp(prefix="zjy5_ck_")
    cm = CookieManager(cookie_path=str(Path(d) / "nope.txt"))
    ok_, msg = cm.check_cookie_online(opener=FakeOpener(NAV_LOGGED_IN))
    eq(ok_, False, "文件不存在应判失败")
    ok("不存在" in msg, f"提示应说明文件不存在，实际：{msg}")


def test_file_without_sessdata():
    d = tempfile.mkdtemp(prefix="zjy5_ck_")
    cm = CookieManager(cookie_path=_write_cookie(d, names=("buvid3",)))
    ok_, msg = cm.check_cookie_online(opener=FakeOpener(NAV_LOGGED_IN))
    eq(ok_, False, "没有 SESSDATA 应判失败")
    ok("SESSDATA" in msg, f"提示应提到缺少 SESSDATA，实际：{msg}")


def test_valid_cookie_returns_username():
    d = tempfile.mkdtemp(prefix="zjy5_ck_")
    cm = CookieManager(cookie_path=_write_cookie(d))
    ok_, msg = cm.check_cookie_online(opener=FakeOpener(NAV_LOGGED_IN))
    eq(ok_, True, "isLogin=true 应判有效")
    ok("测试舰长" in msg, f"应回显用户名，实际：{msg}")


def test_expired_cookie_detected():
    """核心场景：文件里有 SESSDATA，但服务端说没登录。

    ⚠️ 旧实现（只查字符串）在这里会返回 True —— 这就是要修的问题。
    """
    d = tempfile.mkdtemp(prefix="zjy5_ck_")
    cm = CookieManager(cookie_path=_write_cookie(d))
    ok_, msg = cm.check_cookie_online(opener=FakeOpener(NAV_NOT_LOGGED_IN))
    eq(ok_, False, "服务端说未登录应判失败")
    ok("失效" in msg or "过期" in msg or "未登录" in msg,
       f"提示应说明 Cookie 已失效，实际：{msg}")


def test_network_error_not_reported_as_invalid():
    """网络故障 **不能** 被说成「Cookie 无效」。

    否则用户会白白去重新登录 —— 而真实原因是网络不通。
    这是本功能最重要的一条设计约束。
    """
    d = tempfile.mkdtemp(prefix="zjy5_ck_")
    cm = CookieManager(cookie_path=_write_cookie(d))
    ok_, msg = cm.check_cookie_online(
        opener=FakeOpener(fail=OSError("connection refused")))
    eq(ok_, False, "网络失败应判失败（无法确认有效）")
    ok("网络" in msg or "请求" in msg,
       f"提示应指明是网络/请求问题，而不是 Cookie 无效，实际：{msg}")
    ok("失效" not in msg, "不应把网络故障说成 Cookie 失效")


def test_api_error_code_surfaced():
    """接口返回非 0 且不是未登录码时，应把原始信息带出来（便于排查）。"""
    d = tempfile.mkdtemp(prefix="zjy5_ck_")
    cm = CookieManager(cookie_path=_write_cookie(d))
    payload = {"code": -352, "message": "风控校验失败", "data": None}
    ok_, msg = cm.check_cookie_online(opener=FakeOpener(payload))
    eq(ok_, False, "接口报错应判失败")
    ok("352" in msg or "风控" in msg, f"应带出接口错误信息，实际：{msg}")


def test_does_not_mutate_cookie_file():
    """检测是只读操作，不能改动 Cookie 文件（否则可能把凭据写坏）。"""
    d = tempfile.mkdtemp(prefix="zjy5_ck_")
    path = _write_cookie(d)
    before = Path(path).read_bytes()
    cm = CookieManager(cookie_path=path)
    cm.check_cookie_online(opener=FakeOpener(NAV_LOGGED_IN))
    eq(Path(path).read_bytes(), before, "Cookie 文件不应被改动")


def test_offline_is_logged_in_still_string_only():
    """保留并明确旧行为：`is_logged_in()` 仍是「文件里有 SESSDATA」的**本地**判断。

    它不做网络请求（离线可用），所以**不能**用它判断 Cookie 是否有效。
    """
    d = tempfile.mkdtemp(prefix="zjy5_ck_")
    cm = CookieManager(cookie_path=_write_cookie(d))
    eq(cm.is_logged_in(), True, "本地判断：有 SESSDATA")


TESTS = [
    test_missing_cookie_file,
    test_file_without_sessdata,
    test_valid_cookie_returns_username,
    test_expired_cookie_detected,
    test_network_error_not_reported_as_invalid,
    test_api_error_code_surfaced,
    test_does_not_mutate_cookie_file,
    test_offline_is_logged_in_still_string_only,
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
    print(f"\ntest_cookie_manager：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
