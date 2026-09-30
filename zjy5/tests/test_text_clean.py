#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""采集端质量规则的测试（纯标准库，不需要 torch / paddle / cv2）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_text_clean.py

覆盖：规范化、长度红线、长句切分、去重记频次、jsonl 读写、语气打标解析。
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "chibtaici"))

from text_clean import (  # noqa: E402
    build_record,
    check_line,
    clean_text_list,
    dedup_with_counts,
    ends_complete,
    format_quality_report,
    normalize_text,
    read_jsonl,
    split_long_text,
    text_frequencies,
    write_jsonl,
)
from speaker_filter import LlmSpeakerFilter, build_prompt  # noqa: E402


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


class FakeConverter:
    """假 opencc：把「體」换成「体」，用来验证转换器真的被调用。"""

    def convert(self, text):
        return text.replace("體", "体")


def test_normalize():
    eq(normalize_text("  Hello   World  "), "Hello World", "压缩空白")
    eq(normalize_text("Ｈｅｌｌｏ，世界！"), "Hello,世界!", "全角转半角")
    eq(normalize_text("你好。。。"), "你好。", "重复标点压缩")
    eq(normalize_text("真的吗……"), "真的吗……", "省略号保留两个")
    eq(normalize_text("你好\ufeff\u200b世界"), "你好世界", "去零宽字符")
    eq(normalize_text("好开心🎉呀"), "好开心呀", "去表情")
    eq(normalize_text("♪歌词♪"), "歌词", "去音符记号")
    eq(normalize_text("（BGM）开始吧"), "开始吧", "去非台词标注")
    eq(normalize_text("繁體體字", cc_converter=FakeConverter()), "繁体体字", "繁转简")
    eq(normalize_text(None), "", "None 安全")
    # 全角标点被转成半角后仍应能判断「标点收尾」
    ok(ends_complete("他真的来了。"), "句号收尾")
    ok(ends_complete("是这样吗？"), "问号收尾")
    ok(ends_complete("“他来了。”"), "引号包住也算收尾")
    ok(not ends_complete("他没说完"), "没标点不算收尾")


def test_split_and_check():
    text = "第一句话在这里。" * 12          # 96 字，未超限
    eq(len(split_long_text(text, limit=100)), 1, "未超限不切分")

    longer = "一二三四五六七八九十。" * 12   # 144 字，超限
    pieces = split_long_text(longer, limit=60)
    ok(len(pieces) >= 3, f"超长文本应按标点切开，实际 {len(pieces)} 段")
    ok(all(len(p) <= 60 for p in pieces), "每段都不超过上限")
    ok(all(p.endswith("。") for p in pieces), "每段都在标点处断开")

    awkward = "无标点连续文本" * 30          # 240 字，中间没有标点
    hard = split_long_text(awkward, limit=50)
    ok(all(len(p) <= 50 for p in hard), "没有标点时按长度硬断，也不超过上限")

    eq(check_line("好的"), "too_short", "过短")
    eq(check_line("这是一个合格的句子。"), None, "合格")
    eq(check_line("这是一个没有标点的句子"), None, "默认不要求完整句")
    eq(check_line("这是一个没有标点的句子", require_complete=True), "incomplete", "严格模式")
    eq(check_line(""), "empty", "空文本")


def test_clean_and_dedup():
    report = {}
    kept = clean_text_list(
        ["合格的台词句子。", "好", "另一句合格台词。", "合格的台词句子。"],
        report=report,
    )
    eq(kept, ["合格的台词句子。", "另一句合格台词。", "合格的台词句子。"], "清洗保留顺序")
    eq(report.get("too_short"), 1, "过短被计数")
    eq(report.get("kept"), 3, "保留被计数")

    counts = dedup_with_counts(["A", "B", "A", "A", "C"])
    eq(counts, [("A", 3), ("B", 1), ("C", 1)], "去重并记频次")
    eq(list(text_frequencies([{"text": "A"}, {"text": "A"}, {}]).items()), [("A", 2)], "频次统计")
    ok("保留 3 条" in format_quality_report(report), "体检报告包含保留数")


def test_jsonl_roundtrip():
    record = build_record("你好。", video="BV1xx", start=1.23456, end=2.5,
                          channel="ocr", confidence=0.91234, speaker_score=None,
                          source="ocr")
    eq(record["video"], "BV1xx", "溯源：BV 号")
    eq(record["start"], 1.235, "时间保留 3 位")
    eq(record["confidence"], 0.9123, "置信度保留 4 位")
    ok("speaker_score" not in record, "None 字段不写入")
    ok("source" in record, "额外字段保留")

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sub", "out.jsonl")
        written = write_jsonl(path, [record, {"text": ""}, {"text": "第二句。"}])
        eq(written, 2, "空文本不写入")
        back = read_jsonl(path)
        eq(len(back), 2, "读回条数")
        eq(back[0]["text"], "你好。", "读回文本")

        write_jsonl(path, [{"text": "追加。"}], append=True)
        eq(len(read_jsonl(path)), 3, "追加模式")

        bad_path = os.path.join(tmp, "bad.jsonl")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write('{"text": "好的。"}\n这行不是 json\n{"text": "还行。"}\n')
        eq(len(read_jsonl(bad_path)), 2, "坏行跳过，不丢整份文件")
        eq(read_jsonl(os.path.join(tmp, "不存在.jsonl")), [], "文件不存在返回空")


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """假的 ollama 客户端：/api/tags 返回模型列表，/api/generate 返回打分。"""

    def __init__(self, models=("qwen3:8b",), scores=None, fail=False):
        self.models = list(models)
        self.scores = scores or {}
        self.fail = fail
        self.calls = []

    def open(self, url, timeout=None, data=None):
        self.calls.append(url)
        if self.fail:
            raise OSError("connection refused")
        if str(url).endswith("/api/tags"):
            return FakeResponse({"models": [{"name": m} for m in self.models]})
        if self.scores == "garbage":
            return FakeResponse({"response": "我觉得第一句很像，第二句不像。"})
        return FakeResponse({"response": json.dumps(self.scores.get("list", []))})


def test_speaker_filter():
    prompt = build_prompt("爱莉希雅", ["你好呀", "滚开"])
    ok("爱莉希雅" in prompt and "1. 你好呀" in prompt, "prompt 带角色名与编号")

    eq(LlmSpeakerFilter._parse_scores('[{"i":1,"score":5},{"i":2,"score":1}]', 2), [5, 1],
       "标准 JSON 解析")
    eq(LlmSpeakerFilter._parse_scores('结果如下：\n[{"i":1,"score":4}]\n以上。', 1), [4],
       "夹带解释也能解析")
    eq(LlmSpeakerFilter._parse_scores('1 号 {"i":1,"score":9}', 1), [5], "分数夹紧到 1-5")
    eq(LlmSpeakerFilter._parse_scores("完全不是 JSON", 2), None, "无法解析返回 None")

    flt = LlmSpeakerFilter(model="qwen3:8b", threshold=3, opener=FakeOpener())
    ok(flt.check_available(), "ollama 可用")

    flt2 = LlmSpeakerFilter(model="qwen3:8b", threshold=3, batch_size=2,
                            opener=FakeOpener(scores={"list": [{"i": 1, "score": 5},
                                                                {"i": 2, "score": 1}]}))
    entries = [{"text": "像角色说的。"}, {"text": "明显是别人。"}]
    kept, stats = flt2.filter_entries(entries)
    eq([e["text"] for e in kept], ["像角色说的。"], "低分被剔除")
    eq(kept[0]["speaker_score"], 5, "分数写回条目")
    eq(stats["dropped"], 1, "剔除计数")

    # 打标失败时必须「不丢数据」
    flt3 = LlmSpeakerFilter(opener=FakeOpener(fail=True))
    kept3, stats3 = flt3.filter_entries([{"text": "第一句。"}, {"text": "第二句。"}])
    eq(len(kept3), 2, "打标失败时全部保留")
    eq(stats3["unscored"], 2, "未打分计数")

    flt4 = LlmSpeakerFilter(opener=FakeOpener(scores="garbage"))
    kept4, _ = flt4.filter_entries([{"text": "随便一句台词。"}])
    eq(len(kept4), 1, "模型只回自然语言时不误删")


TESTS = [
    test_normalize,
    test_split_and_check,
    test_clean_and_dedup,
    test_jsonl_roundtrip,
    test_speaker_filter,
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
    print(f"\ntest_text_clean：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
