#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""批量字幕收割的离线测试：目标解析、探测、断点续跑、jsonl 落盘。

**全程不联网**：用假的 extractor / crawler 注入，所以 CI 上也能跑。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_harvest.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ZJY5 = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ZJY5, "bilibili_downloader"))

from harvest import (  # noqa: E402
    STATUS_ERROR,
    STATUS_NO_SUBTITLE,
    STATUS_OK,
    STATUS_PROBED,
    STATUS_SKIPPED,
    HarvestState,
    SubtitleHarvester,
    extract_bvid,
    load_url_list,
    looks_like_up_url,
    normalize_target,
)
from subtitle_extractor import SubtitleExtractor  # noqa: E402


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


class FakeExtractor:
    """假字幕提取器：按 URL 里的 BV 号决定「有没有字幕」。"""

    def __init__(self, with_subtitle=(), entries=None, broken=()):
        self.with_subtitle = set(with_subtitle)
        self.broken = set(broken)
        self.entries = entries or [{"text": "第一句。", "start": 1.0, "end": 2.0},
                                   {"text": "第二句。", "start": 3.0, "end": 4.0}]
        self.probe_calls = []
        self.fetch_calls = []

    def probe_subtitle(self, url, quiet=True):
        self.probe_calls.append(url)
        bvid = extract_bvid(url)
        if bvid in self.broken:
            return {"bvid": bvid, "has_subtitle": False, "error": "接口 403"}
        if bvid in self.with_subtitle:
            return {"bvid": bvid, "has_subtitle": True, "lang": "zh-CN",
                    "ai_type": 1, "subtitle_url": f"https://sub/{bvid}.json", "error": None}
        return {"bvid": bvid, "has_subtitle": False, "subtitle_url": None, "error": None}

    def fetch_subtitle_entries(self, video_url=None, subtitle_url=None, info=None):
        self.fetch_calls.append(video_url)
        return list(self.entries), dict(info or {})

    def write_entries_jsonl(self, entries, output_path, **kwargs):
        return SubtitleExtractor.write_entries_jsonl(entries, output_path, **kwargs)


def bvid(index):
    """造一个**格式合法**的 BV 号：`BV1` + 9 位，正好 12 个字符。

    （之前手写成 11 位，正则匹配不上，结果全被判成「无字幕」—— 测试自己踩的坑。）
    """
    return f"BV1test{index:05d}"


class FakeCrawler:
    def __init__(self, videos=None):
        self.videos = videos if videos is not None else [
            {"url": f"https://www.bilibili.com/video/{bvid(i)}",
             "title": f"第 {i} 个视频", "video_id": bvid(i)}
            for i in range(5)
        ]
        self.calls = []

    def _get_channel_id(self, url):
        if "space.bilibili.com" in url:
            return "123456"
        return None

    def get_all_channel_videos(self, channel_id, max_count=0, order="pubdate", **kwargs):
        self.calls.append((channel_id, max_count, order))
        videos = self.videos
        return videos[:max_count] if max_count else videos


def make_harvester(tmp, **kw):
    return SubtitleHarvester(
        output_dir=kw.pop("output_dir", tmp),
        state_path=kw.pop("state_path", os.path.join(tmp, "harvest_state.json")),
        out_format=kw.pop("out_format", "jsonl"),
        rate_limit=kw.pop("rate_limit", 0.0),
        extractor=kw.pop("extractor", None) or FakeExtractor(),
        crawler=kw.pop("crawler", None) or FakeCrawler(),
        sleep_fn=lambda _seconds: None,
        **kw
    )


def test_target_parsing():
    eq(extract_bvid("https://www.bilibili.com/video/BV1xx411c7mD?p=2"), "BV1xx411c7mD", "抠 BV 号")
    eq(extract_bvid("没有 BV 号的文本"), None, "无 BV 号返回 None")
    eq(normalize_target("BV1xx411c7mD"), "https://www.bilibili.com/video/BV1xx411c7mD", "裸 BV 号")
    eq(normalize_target("  https://space.bilibili.com/1  "),
       "https://space.bilibili.com/1", "URL 去空白")
    eq(normalize_target("# 注释行"), None, "注释忽略")
    eq(normalize_target(""), None, "空行忽略")
    ok(looks_like_up_url("https://space.bilibili.com/123456/video"), "识别 UP 空间")
    ok(not looks_like_up_url("https://www.bilibili.com/video/BV1xx411c7mD"), "视频 URL 不是 UP 空间")

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "urls.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 清单\nBV1xx411c7mD\n\nhttps://www.bilibili.com/video/BV1yy411c7mD\n随便一行\n")
        urls = load_url_list(path)
        eq(len(urls), 2, "清单只收有效行")
        eq(urls[0], "https://www.bilibili.com/video/BV1xx411c7mD", "裸 BV 号被补成 URL")


def test_state_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "state.json")
        state = HarvestState(path)
        ok(not state.should_skip("BV1xx411c7mD"), "新状态不跳过")
        state.mark("BV1xx411c7mD", STATUS_OK, sentences=12, lang="zh-CN")
        state.mark("BV1yy411c7mD", STATUS_ERROR, error="403")
        state.save()

        reloaded = HarvestState(path)
        ok(reloaded.should_skip("BV1xx411c7mD"), "已完成要跳过")
        ok(reloaded.should_skip("BV1yy411c7mD"), "失败默认也跳过（避免反复撞墙）")
        ok(not reloaded.should_skip("BV1yy411c7mD", retry_failed=True), "指定重试时不再跳过")
        eq(reloaded.records["BV1xx411c7mD"]["sentences"], 12, "记录详情")

        with open(path, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        broken = HarvestState(path)
        eq(broken.records, {}, "坏状态文件不炸，按空状态继续")


def test_harvest_hit_and_miss():
    with tempfile.TemporaryDirectory() as tmp:
        extractor = FakeExtractor(with_subtitle=[bvid(0), bvid(2)])
        harvester = make_harvester(tmp, extractor=extractor)

        stats = harvester.run(target="https://space.bilibili.com/123456", max_count=5)
        eq(stats.get(STATUS_OK), 2, "命中 2 个")
        eq(stats.get(STATUS_NO_SUBTITLE), 3, "无字幕 3 个")

        jsonl_path = os.path.join(tmp, "subtitles.jsonl")
        ok(os.path.exists(jsonl_path), "生成 jsonl")
        with open(jsonl_path, encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        eq(len(records), 4, "两个视频 × 两句")
        for key in ("text", "video", "start", "end", "channel", "confidence"):
            ok(key in records[0], f"jsonl 字段 {key}")
        eq(records[0]["channel"], "subtitle", "通道标记为 subtitle")
        eq(records[0]["confidence"], None, "B 站不给置信度就写 null")
        eq(records[0]["video"], bvid(0), "带 BV 号溯源")


def test_resume_skips_finished():
    with tempfile.TemporaryDirectory() as tmp:
        extractor = FakeExtractor(with_subtitle=[bvid(0)])
        first = make_harvester(tmp, extractor=extractor)
        first.run(target="https://space.bilibili.com/123456", max_count=5)

        # 第二遍：全部已处理 -> 全部跳过，且**不再发起探测请求**
        extractor2 = FakeExtractor(with_subtitle=[bvid(0)])
        second = make_harvester(tmp, extractor=extractor2)
        stats = second.run(target="https://space.bilibili.com/123456", max_count=5)
        eq(stats.get(STATUS_SKIPPED), 5, "断点跳过 5 个")
        eq(len(extractor2.probe_calls), 0, "跳过时不应再探测")

        # 失败项只有显式 --retry-failed 才重试
        extractor3 = FakeExtractor(broken=[bvid(0), bvid(1)])
        third = make_harvester(tmp, extractor=extractor3, state_path=os.path.join(tmp, "s2.json"))
        third.run(target="https://space.bilibili.com/123456", max_count=2)
        calls_after_first = len(extractor3.probe_calls)
        third.run(target="https://space.bilibili.com/123456", max_count=2)
        eq(len(extractor3.probe_calls), calls_after_first, "失败项默认不重试")
        third.run(target="https://space.bilibili.com/123456", max_count=2, retry_failed=True)
        ok(len(extractor3.probe_calls) > calls_after_first, "--retry-failed 时重新探测")


def test_probe_only_and_formats():
    with tempfile.TemporaryDirectory() as tmp:
        # 推荐的用法：先探测、再正式收割 —— 探测的记录**不能**让后续收割跳过
        extractor = FakeExtractor(with_subtitle=[bvid(0)])
        harvester = make_harvester(tmp, extractor=extractor)
        probe_stats = harvester.run(target="https://space.bilibili.com/123456",
                                    max_count=3, probe_only=True)
        eq(probe_stats.get(STATUS_PROBED), 1, "探测到 1 个有字幕")
        ok(not os.path.exists(os.path.join(tmp, "subtitles.jsonl")), "只探测时不落盘")

        real_stats = harvester.run(target="https://space.bilibili.com/123456", max_count=3)
        eq(real_stats.get(STATUS_OK), 1, "接着正式收割：仍会取正文")
        ok(os.path.exists(os.path.join(tmp, "subtitles.jsonl")), "正式收割落盘")

        # 再探一次：probed 状态在探测模式下算已完成，不会重复探
        again = make_harvester(tmp, extractor=FakeExtractor(with_subtitle=[bvid(0)]))
        repeat = again.run(target="https://space.bilibili.com/123456", max_count=3,
                           probe_only=True)
        eq(repeat.get(STATUS_SKIPPED), 3, "重复探测时全部跳过")

    with tempfile.TemporaryDirectory() as tmp:
        harvester = make_harvester(
            tmp, extractor=FakeExtractor(with_subtitle=[bvid(0)]), out_format="txt"
        )
        harvester.run(target="https://space.bilibili.com/123456", max_count=1)
        txt_path = os.path.join(tmp, "subtitles", f"{bvid(0)}.txt")
        ok(os.path.exists(txt_path), "txt 模式按视频落文件")
        with open(txt_path, encoding="utf-8") as f:
            eq(f.read().split("\n"), ["第一句。", "第二句。"], "txt 一行一句")


def test_urls_file_and_dedup():
    with tempfile.TemporaryDirectory() as tmp:
        list_path = os.path.join(tmp, "urls.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            f.write(f"{bvid(0)}\nhttps://www.bilibili.com/video/{bvid(1)}\n"
                    f"{bvid(0)}\n")   # 重复项
        extractor = FakeExtractor(with_subtitle=[bvid(0), bvid(1)])
        harvester = make_harvester(tmp, extractor=extractor)
        stats = harvester.run(target=list_path)

        eq(stats.get(STATUS_OK), 2, "重复的 BV 只处理一次")
        eq(len(extractor.probe_calls), 2, "重复项被去重")


TESTS = [
    test_target_parsing,
    test_state_roundtrip,
    test_harvest_hit_and_miss,
    test_resume_skips_finished,
    test_probe_only_and_formats,
    test_urls_file_and_dedup,
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
    print(f"\ntest_harvest：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
