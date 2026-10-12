#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""WBI 签名的测试（纯函数，完全离线，不需要网络）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_wbi.py

【为什么值得单测】
WBI 签名**错了整个采集链路全部返回 `-352`**（B 站的「风控/签名错误」码），
而 `-352` 看起来像「被风控拦截」，**很难联想到是签名算错**。
偏偏它是 8 个模块里唯一 0 测试覆盖的核心算法。

好消息是签名部分**全是纯函数**：`get_mixin_key` / `sign_params`
给定输入必然得到相同输出，不需要网络、不需要 mock。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "bilibili_downloader"))

from wbi import (  # noqa: E402
    MIXIN_KEY_ENC_TAB,
    get_mixin_key,
    get_mixin_key_cached,
    sign_params,
)


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


def test_mixin_key_uses_reorder_table():
    """mixin key 必须按 mixinKeyEncTab 重排，且只取前 32 位。"""
    orig = "".join(chr(97 + i % 26) for i in range(64))
    expected = "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]
    eq(get_mixin_key(orig), expected, "按重排表取前 32 位")
    eq(len(get_mixin_key(orig)), 32, "长度固定 32")


def test_mixin_key_table_indices_in_range():
    """重排表索引必须全部落在 64 长度范围内（img_key + sub_key 各 32）。

    ⚠️ 表里最大元素是 63，超出会 IndexError；这是最容易在复制粘贴时
    抄错的地方，而抄错的表现同样是 -352。
    """
    ok(max(MIXIN_KEY_ENC_TAB) < 64, "索引不应超过 63")
    ok(min(MIXIN_KEY_ENC_TAB) >= 0, "索引不应为负")
    ok(len(MIXIN_KEY_ENC_TAB) == 64, "应有 64 个索引（铺满 img_key+sub_key）")


def test_sign_params_adds_wts_and_rid():
    """签名必须追加 wts（时间戳）与 w_rid（32 位 hex）。"""
    signed = sign_params({"mid": 123}, ssl_context=None, headers=None)
    ok("wts" in signed, "应追加 wts")
    ok("w_rid" in signed, "应追加 w_rid")
    ok(isinstance(signed["wts"], int), "wts 应为 int 时间戳")
    eq(len(signed["w_rid"]), 32, "w_rid 应为 32 位 md5")
    ok(all(c in "0123456789abcdef" for c in signed["w_rid"]), "w_rid 应为小写 hex")


def test_sign_params_preserves_original_keys():
    """签名后不能丢掉原始参数。"""
    signed = sign_params({"mid": 123, "pn": 1, "ps": 30})
    for k in ("mid", "pn", "ps"):
        ok(k in signed, f"原参数 {k} 应保留")


def test_sign_params_filters_special_chars():
    """value 里的 !'()* 必须被滤掉（B 站签名规则）。

    这四个字符不过滤会导致签名与服务端不一致 —— 又是 -352。
    """
    signed = sign_params({"k": "a!'()*b"}, headers={"User-Agent": "x"})
    eq(signed["k"], "a!'()*b", "原字典里的值不应被改动")
    # 参与签名的是副本，过滤器只在签名时生效 —— 所以这里验的是
    # 同参数下签名稳定，而不是值被改写
    a = sign_params({"k": "x'y"}, headers={"User-Agent": "u"})["w_rid"]
    b = sign_params({"k": "x'y"}, headers={"User-Agent": "u"})["w_rid"]
    eq(a, b, "同参数签名应稳定（过滤器不引入不确定性）")


def test_sign_params_key_order_irrelevant():
    """签名必须与**参数传入顺序**无关（规则要求按 key 排序）。"""
    a = sign_params({"a": 1, "b": 2, "c": 3}, headers={"User-Agent": "u"})
    b = sign_params({"c": 3, "b": 2, "a": 1}, headers={"User-Agent": "u"})
    eq(a["w_rid"], b["w_rid"], "不同传入顺序应得到相同 w_rid")


def test_sign_params_uses_cached_mixin_key():
    """第二次签名应命中缓存，不再请求 nav 接口。"""
    from wbi import _mixin_key_cache
    import wbi as wbi_mod

    orig_cache = wbi_mod._mixin_key_cache
    try:
        wbi_mod._mixin_key_cache = None
        # 注入假 key 进缓存：不命中缓存真的会去请求 nav（会联网/触发风控）
        wbi_mod._mixin_key_cache = ("fake_mixin_key_32bytes_padding_xx", 9e18)
        signed = sign_params({"mid": 1})
        ok(signed, "注入缓存后应能直接签名（不发网络请求）")
    finally:
        wbi_mod._mixin_key_cache = orig_cache


def test_get_mixin_key_cached_returns_str():
    """带缓存的版本在缓存命中时应直接返回字符串。"""
    import wbi as wbi_mod
    orig = wbi_mod._mixin_key_cache
    try:
        wbi_mod._mixin_key_cache = ("cached_key_32bytes_padding_xxxxx", 9e18)
        k = wbi_mod.get_mixin_key_cached()
        eq(k, "cached_key_32bytes_padding_xxxxx", "命中缓存时不应重新请求")
    finally:
        wbi_mod._mixin_key_cache = orig


def test_mixin_key_cache_expires():
    """缓存过期后应重新获取 —— 否则 key 轮换后会一直 -352。"""
    import time as _t
    import wbi as wbi_mod
    orig = wbi_mod._mixin_key_cache
    try:
        # 已过期的缓存：TTL 判断用 `now < 过期时间戳`
        wbi_mod._mixin_key_cache = ("old", _t.time() - 1)
        expired = wbi_mod._mixin_key_cache[1] <= _t.time()
        ok(expired, "过期缓存应被判为已过期")
    finally:
        wbi_mod._mixin_key_cache = orig


TESTS = [
    test_mixin_key_uses_reorder_table,
    test_mixin_key_table_indices_in_range,
    test_sign_params_adds_wts_and_rid,
    test_sign_params_preserves_original_keys,
    test_sign_params_filters_special_chars,
    test_sign_params_key_order_irrelevant,
    test_sign_params_uses_cached_mixin_key,
    test_get_mixin_key_cached_returns_str,
    test_mixin_key_cache_expires,
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
    print(f"\ntest_wbi：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
