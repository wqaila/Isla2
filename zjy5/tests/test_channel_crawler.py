#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""频道翻页逻辑的测试（不联网：把网络方法换成假数据）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_channel_crawler.py

【为什么只测翻页】
`channel_crawler.py` 里真正容易出 bug 的是**翻页边界**：
最后一页不满、空页、`max_count` 截断、保护性上限。
这些**完全可以喂假数据测**，不需要网络。

其余方法（`get_channel_videos` 等）是纯网络 I/O，
测它们只会变成「测 mock」，价值低 —— 所以不测。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "bilibili_downloader"))

from channel_crawler import ChannelCrawler  # noqa: E402


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


def _make(n, prefix="BV"):
    """造 n 个假视频。"""
    return [{"video_id": f"{prefix}{i:03d}", "title": f"视频{i}"} for i in range(n)]


def _crawler_with_pages(pages):
    """构造一个 crawler，把 get_channel_videos 换成按页返回假数据。

    `pages` 是「第 N 页返回什么」的列表（1-based）。
    记录实际请求过的页码，供断言使用。
    """
    c = ChannelCrawler(cookie_path=None)
    calls = []

    def fake_get(channel_id, page=1, page_size=30, order="pubdate"):
        calls.append(page)
        if page - 1 < len(pages):
            return pages[page - 1]
        return []

    c.get_channel_videos = fake_get          # 替换实例方法
    c._test_calls = calls
    return c


def test_single_full_page_then_empty():
    """第一页满、第二页空 → 应停止，拿到 30 个。"""
    c = _crawler_with_pages([_make(30), []])
    got = c.get_all_channel_videos("123", page_delay=0)
    eq(len(got), 30, "只取到第一页")
    eq(c._test_calls, [1, 2], "应请求到空页才停")


def test_short_page_stops_pagination():
    """不满一页说明到底了 → 不该再请求下一页。"""
    c = _crawler_with_pages([_make(12)])
    got = c.get_all_channel_videos("123", page_delay=0)
    eq(len(got), 12, "取到 12 个")
    eq(c._test_calls, [1], "不应请求第 2 页")


def test_max_count_truncates():
    """max_count 必须精确截断，而不是「多取一页再裁」。"""
    c = _crawler_with_pages([_make(30), _make(30, "BB")])
    got = c.get_all_channel_videos("123", max_count=35, page_delay=0)
    eq(len(got), 35, "应精确截断到 35")
    eq(c._test_calls, [1, 2], "截断发生在第二页取回之后")


def test_max_count_zero_means_unlimited():
    """max_count=0 表示不限，应翻到空页。"""
    c = _crawler_with_pages([_make(30), _make(30, "BB"), _make(30, "CC"), []])
    got = c.get_all_channel_videos("123", max_count=0, page_delay=0)
    eq(len(got), 90, "取满三页")
    eq(c._test_calls, [1, 2, 3, 4], "应请求到空页")


def test_empty_first_page():
    """第一页就空 → 直接返回空，不该死循环。"""
    c = _crawler_with_pages([[]])
    got = c.get_all_channel_videos("123", page_delay=0)
    eq(got, [], "空频道应返回空列表")
    eq(c._test_calls, [1], "只请求一次")


def test_max_pages_guard():
    """保护性上限：接口行为异常（永远返回满页）时不能无限翻。

    ⚠️ 这是**防死循环的兜底**。没有它，一旦接口返回固定满页，
    脚本会一直翻下去 —— 既打爆对方接口，也永远不结束。
    """
    c = _crawler_with_pages([_make(30)] * 500)
    got = c.get_all_channel_videos("123", page_delay=0)
    ok(len(c._test_calls) <= 200, f"请求页数应受限，实际 {len(c._test_calls)}")
    eq(len(got), 30 * 200, "取满保护上限页数")


def test_no_duplicate_across_pages():
    """跨页结果应全部保留（不去重）—— 去重是下游的事。

    这里锁住的是「不要在翻页时意外丢数据」。
    """
    c = _crawler_with_pages([_make(30), _make(30, "BB")])
    got = c.get_all_channel_videos("123", page_delay=0)
    eq(len(got), 60, "两页共 60 个")
    eq(len({v["video_id"] for v in got}), 60, "无重复")


def test_order_passed_through():
    """order 参数应透传给每页请求。"""
    c = ChannelCrawler(cookie_path=None)
    seen = []

    def fake_get(channel_id, page=1, page_size=30, order="pubdate"):
        seen.append(order)
        return [] if page > 1 else _make(30)

    c.get_channel_videos = fake_get
    c.get_all_channel_videos("123", order="hot", page_delay=0)
    ok(all(o == "hot" for o in seen), f"order 应透传，实际 {seen}")


TESTS = [
    test_single_full_page_then_empty,
    test_short_page_stops_pagination,
    test_max_count_truncates,
    test_max_count_zero_means_unlimited,
    test_empty_first_page,
    test_max_pages_guard,
    test_no_duplicate_across_pages,
    test_order_passed_through,
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
    print(f"\ntest_channel_crawler：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
