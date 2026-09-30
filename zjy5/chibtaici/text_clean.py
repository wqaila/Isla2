#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""台词文本的规范化、质量过滤与 jsonl 结构化输出。

**只依赖标准库**，因此可以在没装 torch / paddle / opencv 的环境里单独测试。
(chibtaici 的 `main.py` 一 import 就会探测这些重依赖，纯逻辑放在这里才测的动。)

这里实现的是采集侧的四条「质量红线」：

============  ====================================================
维度           做法
============  ====================================================
规范化         繁→简、全角→半角、去零宽/表情/装饰符、压缩重复标点
长度           小于 min_len 丢弃；超长按标点切成句子（而不是留一条连行）
完整性         可选：丢弃没有标点收尾的截断句（默认关，理由见下）
重复           去重但**记频次**（同一句出现多次是有意义的加权信号）
============  ====================================================

> 为什么 `require_complete` 默认关闭：ASR 输出的整段文本经常没有句末标点，
> 默认丢弃会一次性损失大量语料。需要严格时再显式打开。

溯源信息（video / start / end / channel / confidence）由 :func:`build_record`
统一组装，写入 jsonl —— 纯文本一旦落盘就不可逆，jsonl 是采集端的「责任交接单」。
"""

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "normalize_text",
    "ends_complete",
    "split_long_text",
    "check_line",
    "clean_text_list",
    "dedup_with_counts",
    "text_frequencies",
    "build_record",
    "write_jsonl",
    "read_jsonl",
    "format_quality_report",
    "CHANNEL_OCR",
    "CHANNEL_VOICE",
    "CHANNEL_SUBTITLE",
    "CHANNEL_MERGED",
]

# 通道名（jsonl 的 channel 字段）
CHANNEL_OCR = "ocr"
CHANNEL_VOICE = "voice"
CHANNEL_SUBTITLE = "subtitle"
CHANNEL_MERGED = "voice+ocr"

_SENTENCE_TERMINATORS = "。！？…!?．."
_BREAK_CHARS = "。！？；…!?;，、,"

# 全角 -> 半角（ASCII 可见区 + 全角空格）
_FULLWIDTH_MAP = {c: chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)}
_FULLWIDTH_MAP[0x3000] = " "
_FULLWIDTH_TRANS = str.maketrans(_FULLWIDTH_MAP)

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff\u00ad]")

# 表情、装饰符号、区域指示符、变体选择符 —— 都是一次识别残留
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"     # 各类 emoji / 象形符号
    "\U00002600-\U000027bf"     # 杂项符号与装饰
    "\U0001f1e6-\U0001f1ff"     # 区域指示符（国旗）
    "\u2b00-\u2bff"             # 补充箭头
    "\ufe0f\u20e3"              # 变体选择符 / 键帽
    "]"
)

# 音符记号：多半是把 OP/ED 歌词识别进来了
_MUSIC_MARKS = re.compile(r"[♪♫♬♩]+")

# 括号里的非台词标注（BGM / 掌声这类）
_NON_SPEECH = re.compile(
    r"[（(]\s*"
    r"(?:bgm|ost|音乐|配乐|背景音乐|掌声|笑声|欢呼|音效|特效|画面|镜头|字幕|歌词|咳嗽|叹气|沉默|静音|杂音)"
    r"[^)）]{0,10}"
    r"[)）]",
    re.IGNORECASE,
)

_TRAILING_CLOSERS = "”\"'』」）》)）】］]"

# 开头要去掉的噪声（OCR 常把项目符号、破折号一起框进来）
_LEADING_TRIM = " \t-—–・·、,，.。!！?？：:；;…"

# 结尾只去掉空白与连接符 —— **绝不能去掉句末标点**：
# 那正是「完整句」判断的依据，去掉等于把合格句改成截断句。
_TRAILING_TRIM = " \t-—–・·、,"


def _collapse_punct(text: str) -> str:
    """压缩连续重复的标点：``！！！`` → ``！``，``……`` 保留两个（正当用法）。"""
    out = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in "，。！？、；：,.!?;:":
            out.append(char)
            while index + 1 < length and text[index + 1] == char:
                index += 1
        elif char == "…":
            out.append(char)
            if index + 1 < length and text[index + 1] == "…":
                out.append("…")
                while index + 1 < length and text[index + 1] == "…":
                    index += 1
        else:
            out.append(char)
        index += 1
    return "".join(out)


def normalize_text(text: str, cc_converter: Any = None) -> str:
    """规范化一句台词（不改动顺序与语义，只做等价形式的统一）。

    :param cc_converter: opencc 的转换器（可选）。传了就先做繁→简。
    """
    if text is None:
        return ""

    result = str(text)
    result = _ZERO_WIDTH.sub("", result)
    result = _EMOJI.sub("", result)
    result = _MUSIC_MARKS.sub("", result)
    result = result.translate(_FULLWIDTH_TRANS)

    if cc_converter is not None:
        try:
            result = cc_converter.convert(result)
        except Exception:
            # 转换器有问题不该影响主流程，原样继续
            pass

    result = _NON_SPEECH.sub("", result)
    result = _collapse_punct(result)
    result = re.sub(r"\s+", " ", result).strip()
    result = result.lstrip(_LEADING_TRIM)
    result = result.rstrip(_TRAILING_TRIM).strip()
    return result


def _strip_closers(text: str) -> str:
    """去掉结尾的引号/括号，方便判断是否「标点收尾」。"""
    result = text
    while result and result[-1] in _TRAILING_CLOSERS:
        result = result[:-1]
    return result


def ends_complete(text: str) -> bool:
    """是否以句末标点收尾（结尾的引号、括号不算）。"""
    if not text:
        return False
    return bool(_strip_closers(text)) and _strip_closers(text)[-1] in _SENTENCE_TERMINATORS


def split_long_text(text: str, limit: int = 100, min_piece: int = 4) -> List[str]:
    """把过长的文本按标点切成不超过 ``limit`` 的片段。

    质量红线里「>200 字是连行，按标点切开」说的就是这件事：
    一条把三句话黏在一起的样本，会教模型「一句话可以说很长」。
    """
    content = (text or "").strip()
    if not content:
        return []
    if len(content) <= limit:
        return [content]

    pieces: List[str] = []
    buffer = ""
    for char in content:
        buffer += char
        if len(buffer) < limit:
            continue
        # 到长度上限了：在缓冲区里找最后一个标点作为断点，找不到就硬断
        cut = max(buffer.rfind(c) for c in _BREAK_CHARS)
        if cut >= min_piece:
            pieces.append(buffer[:cut + 1].strip())
            buffer = buffer[cut + 1:]
        else:
            pieces.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        pieces.append(buffer.strip())

    return [p for p in pieces if p]


def check_line(text: str, min_len: int = 4, max_len: int = 100,
               require_complete: bool = False) -> Optional[str]:
    """检查一句话是否合格。合格返回 ``None``，否则返回不合格的原因字符串。"""
    if not text:
        return "empty"
    length = len(text)
    if length < min_len:
        return "too_short"
    if length > max_len:
        return "too_long"
    if require_complete and not ends_complete(text):
        return "incomplete"
    return None


def clean_text_list(texts: Iterable[str], cc_converter: Any = None,
                    min_len: int = 4, max_len: int = 100,
                    require_complete: bool = False,
                    report: Dict[str, int] = None) -> List[str]:
    """对一批文本做「规范化 → 长句切分 → 质量过滤」。

    :param report: 传入 dict 时，会把丢弃原因统计写进去（便于打印体检报告）
    :return: 合格文本列表（已按需切分，未去重）
    """
    if report is None:
        report = {}

    def bump(reason: str) -> None:
        report[reason] = report.get(reason, 0) + 1

    kept: List[str] = []
    for raw in texts:
        normalized = normalize_text(raw, cc_converter=cc_converter)
        if not normalized:
            bump("empty")
            continue
        for piece in split_long_text(normalized, limit=max_len):
            reason = check_line(piece, min_len=min_len, max_len=max_len,
                                require_complete=require_complete)
            if reason:
                bump(reason)
            else:
                kept.append(piece)
                bump("kept")
    return kept


def dedup_with_counts(texts: Iterable[str]) -> List[Tuple[str, int]]:
    """跨句去重但**保留频次**，返回 ``[(text, count)]``（保持首次出现顺序）。

    高频台词通常是角色的口头禅 —— 对训练而言它是有用的加权信号，
    直接删掉反而丢了信息。
    """
    counts: Dict[str, int] = {}
    for text in texts:
        if not text:
            continue
        counts[text] = counts.get(text, 0) + 1
    return list(counts.items())


def text_frequencies(records: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """从 jsonl 记录里统计句子频次（跨视频合并时用）。"""
    counts: Dict[str, int] = {}
    for record in records:
        text = (record or {}).get("text")
        if text:
            counts[text] = counts.get(text, 0) + 1
    return counts


def build_record(text: str, video: str = None, start: float = 0.0, end: float = 0.0,
                 channel: str = CHANNEL_OCR, confidence: float = None,
                 **extra: Any) -> Dict[str, Any]:
    """组装一条 jsonl 记录（句级溯源）。"""
    record: Dict[str, Any] = {
        "text": text,
        "video": video,
        "start": round(float(start or 0.0), 3),
        "end": round(float(end or 0.0), 3),
        "channel": channel,
        "confidence": (round(float(confidence), 4)
                       if isinstance(confidence, (int, float)) else None),
    }
    for key, value in extra.items():
        if value is not None:
            record[key] = value
    return record


def write_jsonl(path: str, records: Sequence[Dict[str, Any]], append: bool = False) -> int:
    """写 jsonl（一行一条记录），返回写入条数。"""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    mode = "a" if append else "w"
    written = 0
    with open(path, mode, encoding="utf-8") as f:
        for record in records:
            if not record or not record.get("text"):
                continue
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
    return written


def read_jsonl(path: str) -> List[Dict[str, Any]]:
    """读 jsonl；坏行跳过并计数（不因为一行坏掉丢掉整份文件）。"""
    records: List[Dict[str, Any]] = []
    if not path or not os.path.exists(path):
        return records
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def format_quality_report(report: Dict[str, int]) -> str:
    """把清洗统计排成一段人能看懂的体检报告。"""
    if not report:
        return ""

    labels = {
        "kept": "保留",
        "empty": "规范化后为空",
        "too_short": "过短（< 下限）",
        "too_long": "过长（未切开）",
        "incomplete": "无标点收尾（严格模式）",
    }
    lines = ["文本质量过滤："]
    kept = report.get("kept", 0)
    total = sum(v for k, v in report.items() if k != "kept")
    lines.append(f"  保留 {kept} 条 / 检查 {kept + total} 条")
    for key in ("empty", "too_short", "too_long", "incomplete"):
        if report.get(key):
            lines.append(f"  丢弃 {report[key]} 条：{labels.get(key, key)}")
    return "\n".join(lines)
