#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""chibtaici 纯逻辑测试：合并策略、SRT 不变量、质量过滤、jsonl 输出。

不加载 cv2 / paddle / whisper —— 只把 RoleLineExtractor 当普通对象用
（`__new__` 造实例 + 手填属性），所以没有 GPU 也能跑。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_merge_and_srt.py
"""

import importlib.util
import json
import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ZJY5 = os.path.dirname(HERE)
CHIB = os.path.join(ZJY5, "chibtaici")

sys.path.insert(0, CHIB)

_spec = importlib.util.spec_from_file_location("chibtaici_main", os.path.join(CHIB, "main.py"))
chibtaici_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chibtaici_main)

RoleLineExtractor = chibtaici_main.RoleLineExtractor


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


def make_extractor(**kw):
    """造一个不碰 cv2 的提取器实例（只填纯逻辑需要的属性）。"""
    obj = RoleLineExtractor.__new__(RoleLineExtractor)
    obj.clean = kw.get("clean", True)
    obj.min_len = kw.get("min_len", 4)
    obj.max_len = kw.get("max_len", 100)
    obj.require_complete = kw.get("require_complete", False)
    obj.skip_head = kw.get("skip_head", 0.0)
    obj.skip_tail = kw.get("skip_tail", 0.0)
    obj.duration = kw.get("duration", 600.0)
    obj.cc_converter = kw.get("cc_converter", None)
    obj.video_id = kw.get("video_id", "BV1test0001")
    obj.speaker_filter = kw.get("speaker_filter", None)
    obj.enable_ocr = False
    obj.enable_voice = False
    obj._clean_report = {}
    return obj


def entry(text, start, end, source="ocr", confidence=None, channel=None):
    return {"text": text, "start": start, "end": end, "source": source,
            "channel": channel or source, "confidence": confidence}


def test_merge_no_concatenation():
    """回归：不同文本绝不能被拼成一条连行。"""
    ex = make_extractor()
    ocr = [entry("第一句台词。", 0.0, 1.0)]
    voice = [entry("第二句完全不同的话。", 1.2, 2.0, source="voice")]

    merged = ex.merge_timeline(ocr, voice)
    eq(len(merged), 2, "两条不同台词各自成条")
    eq(merged[0]["text"], "第一句台词。", "第一条文本不变")
    eq(merged[1]["text"], "第二句完全不同的话。", "第二条文本不变")

    # 旧实现会就地修改调用方传入的列表 —— 这里确认没有副作用
    eq(ocr[0]["text"], "第一句台词。", "入参未被就地修改")
    eq(len(ocr), 1, "入参长度不变")


def test_merge_same_line():
    """OCR 与 ASR 识别到同一句时，应该并成一条而不是拼成两个。"""
    ex = make_extractor()
    ocr = [entry("悲剧并非终结。", 123.0, 125.0)]
    voice = [entry("悲剧并非终结。", 123.4, 126.8, source="voice")]

    merged = ex.merge_timeline(ocr, voice)
    eq(len(merged), 1, "同一句只留一条")
    eq(merged[0]["text"], "悲剧并非终结。", "文本不重复")
    eq(merged[0]["channel"], "voice+ocr", "通道标注为两路合并")
    eq(merged[0]["end"], 126.8, "时间取并集")


def test_merge_similar_line():
    """高度相似（少标点 / 少几个字）也算同一条，取信息量更大的那份。"""
    ex = make_extractor()
    ocr = [entry("悲剧并非终结而是希望的起始", 10.0, 12.0)]
    voice = [entry("悲剧并非终结，而是希望的起始。", 10.2, 12.4, source="voice")]

    merged = ex.merge_timeline(ocr, voice)
    eq(len(merged), 1, "相似句合并")
    ok(len(merged[0]["text"]) >= len(ocr[0]["text"]), "保留更长的文本")

    # 时间隔太远的同文本不该合并（说明是两次出现）
    far = ex.merge_timeline([entry("回来啦。", 0.0, 0.5)],
                            [entry("回来啦。", 30.0, 30.5, source="voice")])
    eq(len(far), 2, "相隔很远的同一句不合并")

    # 短句不能靠「包含」误判：哦 ⊂ 哦是吗，但不是同一句
    ok(not ex._is_same_line("哦", "哦是吗"), "短字串的包含关系不算同一句")


def test_to_srt_invariants():
    ex = make_extractor()
    rng = random.Random(20260928)

    for trial in range(200):
        count = rng.randint(1, 12)
        entries = []
        cursor = 0.0
        for i in range(count):
            cursor += rng.uniform(0.0, 3.0)
            start = cursor
            span = rng.choice([0.0, 0.4, 1.2, 3.0])
            entries.append(entry(f"第{i}句台词。", start, start + span))
            cursor = start + span

        srt = ex.to_srt(entries)
        blocks = [b for b in srt.split("\n\n") if b.strip()]
        eq(len(blocks), count, "块数与条目数一致")

        previous_end = -1.0
        for index, block in enumerate(blocks, 1):
            lines = block.strip().split("\n")
            eq(lines[0], str(index), "索引递增")
            start_text, _, end_text = lines[1].partition(" --> ")
            start = _srt_to_seconds(start_text)
            end = _srt_to_seconds(end_text)
            ok(start < end, f"起 < 止（第 {index} 条）")
            ok(start >= previous_end - 1e-6, f"不重叠（第 {index} 条）")
            previous_end = end

    # OCR 条目原本 start == end，转 SRT 要补出持续时间
    solo = ex.to_srt([entry("只有时间点的台词。", 5.0, 5.0)])
    _, _, end_text = solo.split("\n")[1].partition(" --> ")
    ok(_srt_to_seconds(end_text) > 5.0, "start==end 时补最小持续时间")


def _srt_to_seconds(text):
    hh, mm, rest = text.strip().split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def test_clean_entries():
    ex = make_extractor(skip_head=90.0, skip_tail=90.0, duration=600.0)
    raw = [
        entry("开场就出场的台词。", 10.0, 12.0),        # 掐头 -> 丢弃
        entry("好", 100.0, 100.5),                      # 过短 -> 丢弃
        entry("这是一句正常的台词。", 100.0, 102.0),
        entry("（BGM）背景音乐标注。", 120.0, 121.0),   # 去掉标注后仍合格
        entry("结尾处的台词。", 590.0, 595.0),          # 去尾 -> 丢弃
        entry("", 130.0, 131.0),                        # 空 -> 丢弃
        entry("ＡＢＣ全角字母。", 140.0, 141.0),        # 规范化为半角
    ]

    cleaned = ex.clean_entries(raw)
    texts = [e["text"] for e in cleaned]
    eq(texts, ["这是一句正常的台词。", "背景音乐标注。", "ABC全角字母。"], "过滤结果")
    eq(ex._clean_report.get("too_short"), 1, "过短计数")
    eq(ex._clean_report.get("out_of_window"), 2, "掐头去尾计数")
    eq(ex._clean_report.get("empty"), 1, "空文本计数")

    # 关掉清洗时原样放行
    no_clean = make_extractor(clean=False)
    eq(len(no_clean.clean_entries(raw)), len(raw), "--no-clean 时不过滤")

    # 超长句子被切开而不是丢弃
    long_text = "很长的一句话在这里。" * 20
    split = make_extractor().clean_entries([entry(long_text, 0.0, 10.0)])
    ok(len(split) > 1, "超长台词被切成多条")
    ok(all(len(e["text"]) <= 100 for e in split), "切分后都不超上限")

    # 严格模式：没有标点收尾的句子被丢弃
    strict = make_extractor(require_complete=True).clean_entries(
        [entry("这句话没有标点收尾", 0.0, 1.0), entry("这句话有标点。", 2.0, 3.0)]
    )
    eq([e["text"] for e in strict], ["这句话有标点。"], "严格模式丢截断句")


def test_to_jsonl_records_and_run():
    ex = make_extractor(video_id="BV1xx411c7mD")
    ex.enable_ocr = True
    ex.enable_voice = True
    ex.extract_ocr_lines = lambda: [entry("画面上的一句。", 1.0, 2.0, confidence=0.93)]
    ex.extract_audio_transcription = lambda: [entry("语音识别的一句。", 5.0, 7.0,
                                                    source="voice", confidence=0.8)]

    records = ex.to_jsonl_records(ex.clean_entries(
        ex.merge_timeline(ex.extract_ocr_lines(), ex.extract_audio_transcription())
    ))
    eq(len(records), 2, "两条记录")
    for record in records:
        for key in ("text", "video", "start", "end", "channel", "confidence"):
            ok(key in record, f"jsonl 字段 {key} 存在（句级溯源）")
    eq(records[0]["video"], "BV1xx411c7mD", "带 BV 号")
    eq(records[0]["confidence"], 0.93, "带置信度")

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "role_lines.txt")
        ex.clean = True
        ex._clean_report = {}
        ex.run(output_path=out, fmt="all")

        for path in (out, os.path.join(tmp, "role_lines.srt"),
                     os.path.join(tmp, "role_lines.jsonl")):
            ok(os.path.exists(path), f"生成 {os.path.basename(path)}")

        with open(out, encoding="utf-8") as f:
            eq(f.read().split("\n"), ["画面上的一句。", "语音识别的一句。"], "txt 内容")

        with open(os.path.join(tmp, "role_lines.jsonl"), encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        eq(len(lines), 2, "jsonl 行数")
        eq(lines[0]["channel"], "ocr", "通道标注")


TESTS = [
    test_merge_no_concatenation,
    test_merge_same_line,
    test_merge_similar_line,
    test_to_srt_invariants,
    test_clean_entries,
    test_to_jsonl_records_and_run,
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
    print(f"\ntest_merge_and_srt：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
