#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""下载失败清单的测试（纯标准库，不联网、不启动 yt-dlp）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_download_failures.py

【要解决的问题】
批量下载 100 个视频、第 80 个挂了 —— 现在只会在屏幕上一行 `✗ 下载失败`
然后继续。重跑时只有「文件存在就跳过」这一个信号，
**失败原因与失败清单全部丢失**，无从补齐。

对比：harvest 早就有 `harvest_state.json` 断点续跑 + `--retry-failed`，
而下载器没有。本模块把这块补齐。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "bilibili_downloader"))

from download_failures import (  # noqa: E402
    FailureRecord,
    FailureStore,
    classify_failure,
)


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


def test_record_and_load_roundtrip():
    """记录失败 → 重新加载，内容必须一致（含原因与时间）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "failed.json")
        store = FailureStore(path)
        store.record("https://www.bilibili.com/video/BV1xx411c7mD",
                     reason="返回码 1", title="测试视频")
        store.save()

        reloaded = FailureStore(path)
        eq(len(reloaded.records), 1, "应有一条失败记录")
        rec = reloaded.records[0]
        eq(rec.url, "https://www.bilibili.com/video/BV1xx411c7mD", "URL 保留")
        eq(rec.reason, "返回码 1", "失败原因保留")
        eq(rec.title, "测试视频", "标题保留")
        ok(rec.at, "应记录时间戳")


def test_same_url_not_duplicated():
    """同一个 URL 重复失败只保留一条（并更新原因与次数）。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = FailureStore(os.path.join(tmp, "f.json"))
        store.record("https://x/BV1xx411c7mD", reason="第一次")
        store.record("https://x/BV1xx411c7mD", reason="第二次")
        eq(len(store.records), 1, "同一 URL 不重复")
        eq(store.records[0].reason, "第二次", "原因应更新为最新")
        eq(store.records[0].attempts, 2, "应累计尝试次数")


def test_success_clears_failure():
    """成功后应从失败清单里移除 —— 否则清单会越来越脏。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = FailureStore(os.path.join(tmp, "f.json"))
        url = "https://x/BV1xx411c7mD"
        store.record(url, reason="挂了")
        eq(len(store.records), 1, "先有一条失败")

        removed = store.clear(url)
        ok(removed, "clear 应返回 True 表示确实删掉了")
        eq(len(store.records), 0, "成功后应移除")
        # 再 clear 一次：不存在时应返回 False（便于调用方判断）
        ok(not store.clear(url), "不存在的条目 clear 返回 False")


def test_missing_file_is_empty_not_error():
    """文件不存在时按空清单继续，不能抛异常（首次运行就是这种情况）。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = FailureStore(os.path.join(tmp, "nope.json"))
        eq(store.records, [], "文件不存在 → 空清单")


def test_corrupt_file_does_not_crash():
    """坏 JSON 不能让整个下载流程崩掉（宁可丢清单也不能中断下载）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "bad.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        store = FailureStore(path)
        eq(store.records, [], "坏文件 → 按空清单继续")


def test_save_is_atomic():
    """保存必须「先写临时文件再替换」，中途被打断不会留下半个坏文件。

    （与 harvest 的 `HarvestState.save` 同一做法 —— 状态文件写坏了
    比没有状态更糟，因为它会让下次运行误判。）
    """
    import inspect
    src = inspect.getsource(FailureStore.save)
    ok("os.replace" in src, "应使用 os.replace 原子替换")
    ok(".tmp" in src, "应写临时文件")


def test_classify_failure():
    """失败原因应能分类，便于用户判断该重试还是该改配置。

    分类的意义：`--retry-failed` 对「Cookie 过期」是无用的（重试多少次都一样），
    但对「网络超时」有用。不分类的话用户只能盲目重试。
    """
    eq(classify_failure("ERROR: HTTP Error 403: Forbidden"), "http_403", "403")
    eq(classify_failure("返回码 1"), "unknown", "无特征归为 unknown")
    ok(classify_failure("-352 风控") == "risk_control", "风控单独一类")
    ok(classify_failure("HTTP Error 404") == "not_found", "404 单独一类")
    ok(classify_failure("证书校验失败") == "ssl", "SSL 单独一类")
    ok(classify_failure("连接超时 timeout") == "network", "网络类")
    eq(classify_failure(""), "unknown", "空串安全")
    eq(classify_failure(None), "unknown", "None 安全")


def test_urls_for_retry():
    """应能导出「待重试的 URL 列表」，供 `--retry-failed` 使用。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = FailureStore(os.path.join(tmp, "f.json"))
        store.record("https://x/A", reason="a")
        store.record("https://x/B", reason="b")
        urls = store.urls()
        eq(sorted(urls), ["https://x/A", "https://x/B"], "导出全部 URL")


def test_store_is_readable_json():
    """落盘格式应是可读 JSON（便于人工查看/编辑）。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "f.json")
        store = FailureStore(path)
        store.record("https://x/A", reason="挂了", title="标题")
        store.save()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)          # 必须是合法 JSON
        ok(isinstance(data, dict), "顶层是对象")
        ok("records" in data, "含 records")
        ok("version" in data, "含 version（便于将来迁移格式）")


TESTS = [
    test_record_and_load_roundtrip,
    test_same_url_not_duplicated,
    test_success_clears_failure,
    test_missing_file_is_empty_not_error,
    test_corrupt_file_does_not_crash,
    test_save_is_atomic,
    test_classify_failure,
    test_urls_for_retry,
    test_store_is_readable_json,
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
    print(f"\ntest_download_failures：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
