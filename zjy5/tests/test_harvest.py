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
    STATUS_RISK_CONTROL,
    STATUS_SKIPPED,
    HarvestState,
    SubtitleHarvester,
    extract_bvid,
    is_risk_control_error,
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

    def __init__(self, with_subtitle=(), entries=None, broken=(),
                 broken_error="接口 403"):
        self.with_subtitle = set(with_subtitle)
        self.broken = set(broken)
        # 让测试能区分「普通错误」与「风控」——两者必须走不同分支
        self.broken_error = broken_error
        self.entries = entries or [{"text": "第一句。", "start": 1.0, "end": 2.0},
                                   {"text": "第二句。", "start": 3.0, "end": 4.0}]
        self.probe_calls = []
        self.fetch_calls = []

    def probe_subtitle(self, url, quiet=True):
        self.probe_calls.append(url)
        bvid = extract_bvid(url)
        if bvid in self.broken:
            return {"bvid": bvid, "has_subtitle": False, "error": self.broken_error}
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
        # 默认不真睡；需要断言限速行为的用例显式传入记录器
        sleep_fn=kw.pop("sleep_fn", lambda _seconds: None),
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


def _make_sleep_recorder():
    """返回 (sleep_fn, 记录列表)：每次睡眠把秒数记下来。"""
    slept = []

    def fn(seconds):
        slept.append(seconds)

    return fn, slept


def test_rate_limit_applies_to_probe_only():
    """⚠️ 回归：`--probe-only` 原来**完全绕过限速**。

    旧实现只在 `status == STATUS_OK and not probe_only` 时 sleep，
    于是「探测 1000 个视频」= 1000 个请求**连打、中间零间隔** ——
    这是最容易触发风控的用法，而文档却写着「默认 1 秒限速」。

    限速必须对**每一次实际发出的请求**生效，与模式无关。
    """
    with tempfile.TemporaryDirectory() as tmp:
        sleep_fn, slept = _make_sleep_recorder()
        extractor = FakeExtractor(with_subtitle=[bvid(0), bvid(1)])
        harvester = make_harvester(tmp, extractor=extractor,
                                   rate_limit=1.0, sleep_fn=sleep_fn)
        harvester.run(target="https://space.bilibili.com/123456",
                      max_count=5, probe_only=True)

        eq(len(extractor.probe_calls), 5, "探测了 5 个")
        eq(len(slept), 5, "每次探测后都应限速（原来这里是 0 次）")


def test_rate_limit_applies_to_failures_and_misses():
    """失败与「无字幕」也要限速 —— 它们同样发出了请求。

    旧实现只对 `STATUS_OK` 睡，于是「一个 UP 主 90% 视频没字幕」时，
    实际请求速率远超 `--rate-limit` 的承诺。
    """
    with tempfile.TemporaryDirectory() as tmp:
        sleep_fn, slept = _make_sleep_recorder()
        # 全部「无字幕」：一次命中都没有
        harvester = make_harvester(tmp, extractor=FakeExtractor(),
                                   rate_limit=1.0, sleep_fn=sleep_fn)
        harvester.run(target="https://space.bilibili.com/123456", max_count=4)
        eq(len(slept), 4, "4 个请求都要限速")

    with tempfile.TemporaryDirectory() as tmp:
        sleep_fn, slept = _make_sleep_recorder()
        # 全部失败（如接口 403）
        extractor = FakeExtractor(broken=[bvid(i) for i in range(3)])
        harvester = make_harvester(tmp, extractor=extractor,
                                   rate_limit=1.0, sleep_fn=sleep_fn)
        harvester.run(target="https://space.bilibili.com/123456", max_count=3)
        eq(len(slept), 3, "失败的请求也发出去了，同样要限速")


def test_rate_limit_zero_disables_sleeping():
    """`--rate-limit 0` 应完全不睡（用户显式选择不限速）。"""
    with tempfile.TemporaryDirectory() as tmp:
        sleep_fn, slept = _make_sleep_recorder()
        harvester = make_harvester(tmp, extractor=FakeExtractor(),
                                   rate_limit=0.0, sleep_fn=sleep_fn)
        harvester.run(target="https://space.bilibili.com/123456", max_count=3)
        eq(slept, [], "限速为 0 时不睡")


def test_rate_limit_skips_do_not_sleep():
    """断点跳过的条目**没有发请求**，不该占用限速。"""
    with tempfile.TemporaryDirectory() as tmp:
        first = make_harvester(tmp, extractor=FakeExtractor(with_subtitle=[bvid(0)]))
        first.run(target="https://space.bilibili.com/123456", max_count=3)

        sleep_fn, slept = _make_sleep_recorder()
        second = make_harvester(tmp, extractor=FakeExtractor(with_subtitle=[bvid(0)]),
                                rate_limit=1.0, sleep_fn=sleep_fn)
        stats = second.run(target="https://space.bilibili.com/123456", max_count=3)
        eq(stats.get(STATUS_SKIPPED), 3, "全部跳过")
        eq(slept, [], "跳过不发请求，因此不该睡")


def test_risk_control_detected_in_error_text():
    """`-352` 是风控码，必须与普通网络错误**分开**。

    现状：`harvest.py` 只有一个 `STATUS_ERROR`，且全库**没有任何地方
    判断 -352**。风控与超时被归成一类，后果是「继续跑只会加重风控」。
    """
    ok(is_risk_control_error("接口返回 -352"), "识别 -352")
    ok(is_risk_control_error("风控校验失败"), "识别「风控」字样")
    ok(is_risk_control_error("请求被拦截，请稍后再试"), "识别「拦截」")
    ok(not is_risk_control_error("接口 403"), "403 不算风控")
    ok(not is_risk_control_error("连接超时"), "超时不算风控")
    ok(not is_risk_control_error(None), "None 安全")
    ok(not is_risk_control_error(""), "空串安全")


def test_harvest_stops_after_consecutive_risk_control():
    """连续 3 次风控 → **立即停止整批**，而不是继续撞墙。

    理由：风控通常是会话级/IP 级的，下一个视频多半也会失败；
    停下来能保住已收到的结果，也避免把风控升级成封禁。

    容忍「连续 3 次」是为了放过偶发：单个视频的异常不该中断整批。
    """
    with tempfile.TemporaryDirectory() as tmp:
        extractor = FakeExtractor(
            broken=[bvid(i) for i in range(5)],
            broken_error="-352 风控校验失败",
        )
        harvester = make_harvester(tmp, extractor=extractor, rate_limit=0.0)
        stats = harvester.run(target="https://space.bilibili.com/123456", max_count=5)

        eq(stats.get(STATUS_RISK_CONTROL), 3, "记录到 3 次风控后停止")
        eq(len(extractor.probe_calls), 3, "第 4、5 个不该再发请求")
        ok(harvester.stopped_by_risk_control, "应标记「因风控中断」")


def test_risk_control_counter_resets_on_success():
    """偶发风控不该累积到中断整批：中间有一次成功就重新计数。"""
    with tempfile.TemporaryDirectory() as tmp:
        # 风控、风控、成功、风控、风控 —— 永远不到「连续 3 次」
        extractor = FakeExtractor(
            with_subtitle=[bvid(2)],
            broken=[bvid(0), bvid(1), bvid(3), bvid(4)],
            broken_error="-352 风控",
        )
        harvester = make_harvester(tmp, extractor=extractor, rate_limit=0.0)
        stats = harvester.run(target="https://space.bilibili.com/123456", max_count=5)

        eq(len(extractor.probe_calls), 5, "5 个都应被处理（未触发连续 3 次）")
        eq(stats.get(STATUS_OK), 1, "中间的成功计入")
        ok(not harvester.stopped_by_risk_control, "不该被中断")


def test_normal_errors_do_not_trigger_stop():
    """普通错误（403/超时）**不该**触发中断 —— 只记录，继续跑。"""
    with tempfile.TemporaryDirectory() as tmp:
        extractor = FakeExtractor(broken=[bvid(i) for i in range(5)],
                                  broken_error="接口 403")
        harvester = make_harvester(tmp, extractor=extractor, rate_limit=0.0)
        stats = harvester.run(target="https://space.bilibili.com/123456", max_count=5)

        eq(len(extractor.probe_calls), 5, "普通错误不中断整批")
        eq(stats.get(STATUS_ERROR), 5, "全部记为普通 error")
        ok(not harvester.stopped_by_risk_control, "不该标记为风控中断")


TESTS = [
    test_target_parsing,
    test_state_roundtrip,
    test_harvest_hit_and_miss,
    test_resume_skips_finished,
    test_probe_only_and_formats,
    test_urls_file_and_dedup,
    test_rate_limit_applies_to_probe_only,
    test_rate_limit_applies_to_failures_and_misses,
    test_rate_limit_zero_disables_sleeping,
    test_rate_limit_skips_do_not_sleep,
    test_risk_control_detected_in_error_text,
    test_harvest_stops_after_consecutive_risk_control,
    test_risk_control_counter_resets_on_success,
    test_normal_errors_do_not_trigger_stop,
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
