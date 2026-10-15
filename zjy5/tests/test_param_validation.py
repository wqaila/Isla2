#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""参数校验与退出码的测试（纯标准库，不联网）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_param_validation.py

【要解决的两个「静默失败」】

**一、`--order` 静默兜底**
两个爬虫模块的 order 语义**完全不同**，且未知值都**悄悄降级**：

| 模块 | 支持的值 | 未知值降级成 |
|------|---------|-------------|
| `channel_crawler` | pubdate / latest / hot / view | `1`（最新发布） |
| `search_crawler` | totalrank / click / pubdate / stow | `0`（综合排序） |

而 CLI 的 help 宣称「pubdate/hot/view/totalrank/click/stow」——
把两套混在一起。用户给频道传 `totalrank`、或给搜索传 `hot`，
都会**静默用错排序**，且毫无提示。

**二、批量下载的退出码**
`--channel` / `--search` / `--playlist` 三个分支只 print 不返回状态码，
所以「批量下载全部失败」时**退出码仍是 0**，CI 无法感知。
（单视频下载的退出码已在之前修好。）
"""

import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "bilibili_downloader"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir))

import channel_crawler  # noqa: E402
import search_crawler   # noqa: E402


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


# ---------------------------------------------------------------- order 校验

def test_channel_order_map_exposed():
    """频道排序的合法值应作为模块常量暴露（供校验与文档共用）。"""
    ok(hasattr(channel_crawler, "CHANNEL_ORDERS"),
       "应暴露 CHANNEL_ORDERS 常量")
    for v in ("pubdate", "latest", "hot", "view"):
        ok(v in channel_crawler.CHANNEL_ORDERS, f"应支持 {v}")


def test_search_order_map_exposed():
    """搜索排序的合法值同理。"""
    ok(hasattr(search_crawler, "SEARCH_ORDERS"), "应暴露 SEARCH_ORDERS 常量")
    for v in ("totalrank", "click", "pubdate", "stow"):
        ok(v in search_crawler.SEARCH_ORDERS, f"应支持 {v}")


def test_resolve_channel_order_rejects_unknown():
    """未知的频道排序值应**报错**，而不是静默降级。

    ⚠️ 这是核心回归：原来 `order_map.get(order, 1)` 会悄悄用「最新发布」，
    用户以为按热度排了，其实不是。
    """
    f = getattr(channel_crawler, "resolve_channel_order", None)
    ok(f is not None, "应提供 resolve_channel_order")
    eq(f("hot"), 3, "已知值正常映射")
    eq(f("pubdate"), 1, "已知值正常映射")
    # 未知值：应抛异常或返回明确的失败信号，而不是悄悄给 1
    try:
        got = f("totalrank")          # 这是**搜索**的值，不该被频道接受
    except ValueError:
        return                        # 抛异常 = 正确行为
    raise Missing(f"未知排序值应报错，实际静默返回 {got}")


def test_resolve_search_order_rejects_unknown():
    """未知的搜索排序值同样应报错。"""
    f = getattr(search_crawler, "resolve_search_order", None)
    ok(f is not None, "应提供 resolve_search_order")
    eq(f("totalrank"), 0, "已知值正常映射")
    eq(f("click"), 1, "已知值正常映射")
    try:
        got = f("hot")                # 这是**频道**的值，不该被搜索接受
    except ValueError:
        return
    raise Missing(f"未知排序值应报错，实际静默返回 {got}")


def test_order_error_message_is_actionable():
    """报错信息要**列出可用值**，否则用户还是不知道填什么。"""
    f = channel_crawler.resolve_channel_order
    try:
        f("不存在")
    except ValueError as e:
        msg = str(e)
        ok("pubdate" in msg and "hot" in msg,
           f"报错应列出可用值，实际：{msg}")
        return
    raise Missing("应抛 ValueError")


def test_cli_order_help_matches_channel_values():
    """CLI 的 `--order` help 不该把两套值混在一起宣称。

    ⚠️ 原 help 写「pubdate/hot/view/totalrank/click/stow」，
    但其中 totalrank/click/stow 是**搜索**专用，频道用会静默降级。
    """
    import main as cli_mod
    src = inspect.getsource(cli_mod)
    i = src.find('"--order"')
    ok(i != -1, "应能找到 --order 参数")
    seg = src[i:i + 400]
    # help 里应说明「频道/搜索可用值不同」
    ok("搜索" in seg or "search" in seg.lower(),
       f"--order 的 help 应说明频道与搜索的可选值不同，实际：{seg[:200]}")


# ---------------------------------------------------------------- 退出码

def test_batch_branches_return_nonzero_on_failure():
    """`--channel` / `--search` / `--playlist` 失败时必须返回非 0。

    ⚠️ 这三个分支原来只 print 不返回状态码 → 「批量下载全部失败」
    时退出码仍是 0，CI 无法感知。

    ⚠️ 这里**不用源码字符串搜索** —— 第一版在 `rfind` 位置后 900 字符内
    找 `"return 1"`，但**频道分支的 `return 1` 恰好在同一窗口内**
    （两个分支相邻），所以把「合集分支」的 return 删掉后断言**仍然成立**
    → 变异抓不到。改为**行为断言**：真的跑一遍合集分支看返回值。
    """
    import io
    import main as cli_mod
    from contextlib import redirect_stdout
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmp:
        fake = mock.MagicMock()
        fake.download_playlist.return_value = []        # 一个都没下到
        with mock.patch.object(cli_mod, "BilibiliDownloader", return_value=fake), \
             mock.patch.object(cli_mod, "SubtitleExtractor", return_value=mock.MagicMock()), \
             mock.patch.object(cli_mod.CookieManager, "is_logged_in", return_value=False):
            with redirect_stdout(io.StringIO()):
                rc = cli_mod.main_cli([
                    "--playlist",
                    "https://www.bilibili.com/video/BV1xx411c7mD",
                    "--output", tmp,
                ])
        ok(rc not in (0, None),
           f"合集下载结果为 0 个时应返回非 0，实际 {rc!r}")


def test_channel_failure_detected_by_empty_result():
    """行为断言：频道下载返回空列表时，main_cli 应返回非 0。"""
    import io
    import main as cli_mod
    from contextlib import redirect_stdout
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmp:
        fake = mock.MagicMock()
        fake.download_by_url.return_value = []          # 一个都没下到
        with mock.patch.object(cli_mod, "ChannelDownloader", return_value=fake), \
             mock.patch.object(cli_mod, "SubtitleExtractor", return_value=mock.MagicMock()), \
             mock.patch.object(cli_mod, "BilibiliDownloader", return_value=mock.MagicMock()), \
             mock.patch.object(cli_mod.CookieManager, "is_logged_in", return_value=False):
            with redirect_stdout(io.StringIO()):
                rc = cli_mod.main_cli([
                    "--channel", "https://space.bilibili.com/123",
                    "--output", tmp,
                ])
        ok(rc not in (0, None),
           f"频道下载结果为 0 个时应返回非 0，实际 {rc!r}")


def test_search_failure_detected_by_empty_result():
    """行为断言：搜索下载返回空列表时也应返回非 0。"""
    import io
    import main as cli_mod
    from contextlib import redirect_stdout
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmp:
        fake = mock.MagicMock()
        fake.download_search_results.return_value = []
        with mock.patch.object(cli_mod, "SearchDownloader", return_value=fake), \
             mock.patch.object(cli_mod, "SubtitleExtractor", return_value=mock.MagicMock()), \
             mock.patch.object(cli_mod, "BilibiliDownloader", return_value=mock.MagicMock()), \
             mock.patch.object(cli_mod.CookieManager, "is_logged_in", return_value=False):
            with redirect_stdout(io.StringIO()):
                rc = cli_mod.main_cli(["--search", "关键词", "--output", tmp])
        ok(rc not in (0, None),
           f"搜索下载结果为 0 个时应返回非 0，实际 {rc!r}")


def test_success_still_returns_zero():
    """成功（有下载结果）时应返回 0/None —— 别把成功也判成失败。"""
    import io
    import main as cli_mod
    from contextlib import redirect_stdout
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmp:
        fake = mock.MagicMock()
        fake.download_by_url.return_value = [os.path.join(tmp, "a.mp4")]
        with mock.patch.object(cli_mod, "ChannelDownloader", return_value=fake), \
             mock.patch.object(cli_mod, "SubtitleExtractor", return_value=mock.MagicMock()), \
             mock.patch.object(cli_mod, "BilibiliDownloader", return_value=mock.MagicMock()), \
             mock.patch.object(cli_mod.CookieManager, "is_logged_in", return_value=False):
            with redirect_stdout(io.StringIO()):
                rc = cli_mod.main_cli([
                    "--channel", "https://space.bilibili.com/123", "--output", tmp])
        ok(rc in (0, None), f"成功时应返回 0/None，实际 {rc!r}")


def test_max_rejects_nonpositive():
    """`--max` 必须 ≥1；传 0 或负数应报错退出，而不是静默跑出 0 个结果。

    ⚠️ 原来没有任何校验：`--max -1` 会一路传到搜索接口，
    结果报「API 返回错误」，用户完全看不出是参数问题。
    """
    import io
    import main as cli_mod
    from contextlib import redirect_stdout, redirect_stderr
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmp:
        for bad in ("-1", "0"):
            err = io.StringIO()
            with mock.patch.object(cli_mod, "SearchDownloader", return_value=mock.MagicMock()), \
                 mock.patch.object(cli_mod, "BilibiliDownloader", return_value=mock.MagicMock()), \
                 mock.patch.object(cli_mod.CookieManager, "is_logged_in", return_value=False):
                with redirect_stdout(io.StringIO()), redirect_stderr(err):
                    rc = cli_mod.main_cli(
                        ["--search", "x", "--max", bad, "--output", tmp])
            ok(rc == 2, f"--max {bad} 应返回 2（参数错误），实际 {rc!r}")
            ok("--max" in err.getvalue(),
               f"--max {bad} 的报错应指名参数，实际：{err.getvalue()[:80]}")


def test_max_count_rejects_negative():
    """`--max-count` 不能为负（0 是合法的「不限」）。"""
    import io
    import main as cli_mod
    from contextlib import redirect_stdout, redirect_stderr
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmp:
        err = io.StringIO()
        with mock.patch.object(cli_mod, "BilibiliDownloader", return_value=mock.MagicMock()), \
             mock.patch.object(cli_mod, "SubtitleExtractor", return_value=mock.MagicMock()), \
             mock.patch.object(cli_mod.CookieManager, "is_logged_in", return_value=False):
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = cli_mod.main_cli(
                    ["--harvest", "x", "--max-count", "-5", "--output", tmp])
        ok(rc == 2, f"--max-count -5 应返回 2，实际 {rc!r}")
        ok("--max-count" in err.getvalue(), "报错应指名 --max-count")


def test_unknown_order_rejected_early():
    """完全不认识的 order 应在**参数校验阶段**就被拦下（早于任何网络请求）。"""
    import io
    import main as cli_mod
    from contextlib import redirect_stdout, redirect_stderr
    from unittest import mock

    with tempfile.TemporaryDirectory() as tmp:
        err = io.StringIO()
        fake = mock.MagicMock()
        with mock.patch.object(cli_mod, "SearchDownloader", return_value=fake), \
             mock.patch.object(cli_mod, "BilibiliDownloader", return_value=mock.MagicMock()), \
             mock.patch.object(cli_mod.CookieManager, "is_logged_in", return_value=False):
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = cli_mod.main_cli(
                    ["--search", "x", "--order", "完全不存在", "--output", tmp])
        eq(rc, 2, "未知 order 应返回 2")
        ok("--order" in err.getvalue(), "报错应指名 --order")
        ok("totalrank" in err.getvalue() and "hot" in err.getvalue(),
           f"应同时列出频道与搜索的可用值，实际：{err.getvalue()[:120]}")
        ok(not fake.download_search_results.called,
           "参数非法时不该发起下载请求")


TESTS = [
    test_channel_order_map_exposed,
    test_search_order_map_exposed,
    test_resolve_channel_order_rejects_unknown,
    test_resolve_search_order_rejects_unknown,
    test_order_error_message_is_actionable,
    test_cli_order_help_matches_channel_values,
    test_max_rejects_nonpositive,
    test_max_count_rejects_negative,
    test_unknown_order_rejected_early,
    test_batch_branches_return_nonzero_on_failure,
    test_channel_failure_detected_by_empty_result,
    test_search_failure_detected_by_empty_result,
    test_success_still_returns_zero,
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
    print(f"\ntest_param_validation：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
