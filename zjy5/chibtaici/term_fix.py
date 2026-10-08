#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OCR 术语校正 —— 把识别错的角色名/专有名词改回来。

【为什么必须在采集端做】
实测语料里 OCR 错字很严重（计数来自 2026-10-08 全量统计）：

| 正确   | 次数 | 错写     | 次数 |
|--------|------|----------|------|
| 芽衣   | 771  | 芽依     | 43   |
| 读心术 | 6    | **独心术** | **7** ← 错的多过对的 |
| 千劫   | 77   | 千吉     | 20   |
| 伊甸   | 103  | 依电     | 9    |

这些错字**下游很难修** —— 下游只看到「芽依」，不知道正确的是「芽衣」，
只能靠猜。而采集端有原图、有时间点，能回溯确认。
（这也是为什么「采集端能修的，落盘后就补不回来」。）

【设计取舍：只做**已验证**的替换，不做自动模糊纠正】
模糊匹配（编辑距离 ≤1）听起来很美，但**很容易过度纠正**：
「芽衣」和「芽依」差一字该改，可「伊甸」和一堆差一字的普通词就不该动。
分不清就会把正确文本改坏 —— 那比不改更糟。

所以本模块：
- `fix_terms()`  只做 `KNOWN_ERRORS` 里**实测确认过**的替换（保守、安全）
- `find_suspicious()` 只**报告**疑似错写，不自动改，供人工确认后加进表里

想扩展时：跑一遍 `find_suspicious`，人工确认，再加进 `KNOWN_ERRORS`。
"""
import re
from collections import Counter
from typing import Dict, Iterable, List, Tuple

__all__ = ["TERMS", "KNOWN_ERRORS", "fix_terms", "find_suspicious", "audit_corpus"]

# ---------------------------------------------------------------- 术语表
#
# 用于「检测疑似错写」的规范术语。**加词时要小心**：两个字的词太多，
# 会让 find_suspicious 报一堆误报（比如任何 2 字词都跟「芽衣」差 1 字）。
# 所以这里只放**确实容易识别错**的专有名词。
TERMS = [
    # 角色名
    "爱莉希雅", "芽衣", "凯文", "伊甸", "梅比乌斯", "格蕾修", "克莱因",
    "阿波尼亚", "千劫", "科斯魔", "戴斯多比亚", "维尔薇", "苏莎",
    # 专有名词
    "逐火十三英桀", "英桀", "律者", "人之律者", "始源", "往世乐土",
    "读心术", "舰长", "崩坏",
]

# ---------------------------------------------------------------- 已确认的错写
#
# ⚠️ 每一条都必须是**实测计数确认过**的（错写次数 / 正确次数已记录），
#    不要凭感觉加 —— 改错比不改更糟。
#    格式：错写 -> 正确写法
KNOWN_ERRORS: Dict[str, str] = {
    "芽依": "芽衣",        # 43 / 771
    "独心术": "读心术",     # 7 / 6   ← 错的多过对的，最该修
    "千吉": "千劫",        # 20 / 77
    "千杰": "千劫",        # 48 / 77  ← 上下文确认：「千杰呀，他其实也没有说人坏话」
    "依电": "伊甸",        # 9 / 103
    "美比乌斯": "梅比乌斯",   # 12 / — ← 上下文确认：「美比乌斯博士」
    "苏贞开双": "苏睁开双",   # 27 / — ← 不是名字错，是「苏」+「睁开双眼」，
                            #           「睁」被识别成「贞」
    # ---- 以下为「疑似」但**未经确认**，暂不启用 ----
    # "子子": "梓梓",      # 19 / 12 —— 哪个是对的没确认（可能是昵称）
    # "缘之律者": "人之律者",  # 9 / —  —— 上下文不足，可能是另一个正式称呼
    # "中原的反面": "使人之律者的反面",   # 上下文不明，不敢改
}

# 把长词放前面，避免短词先匹配吃掉长词的一部分
_SORTED = sorted(KNOWN_ERRORS.items(), key=lambda kv: -len(kv[0]))


def fix_terms(text: str, extra: Dict[str, str] = None) -> str:
    """按已确认的错写表替换。没有把握的**不猜**。"""
    if not text:
        return text
    table = _SORTED if not extra else sorted(
        {**KNOWN_ERRORS, **extra}.items(), key=lambda kv: -len(kv[0]))
    for wrong, right in table:
        if wrong in text:
            text = text.replace(wrong, right)
    return text


def _edit_distance_le1(a: str, b: str) -> bool:
    """判断两个字符串是否只差一个字符（替换/增/删）。"""
    if a == b:
        return False
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    short, long = (a, b) if len(a) < len(b) else (b, a)
    i = j = 0
    skipped = False
    while i < len(short) and j < len(long):
        if short[i] == long[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


def find_suspicious(texts: Iterable[str], min_len: int = 2) -> List[Tuple[str, str, int]]:
    """找出「和术语只差一个字」的片段，返回 [(疑似错写, 最像的术语, 次数)]。

    ⚠️ 只报告不修改 —— 差一个字可能是错写，也可能是个完全正常的词。
    人工确认之后再加进 `KNOWN_ERRORS`。
    """
    known_wrong = set(KNOWN_ERRORS)
    candidates: Counter = Counter()
    for text in texts:
        if not text:
            continue
        # 用滑动窗口取和术语等长的片段
        for term in TERMS:
            n = len(term)
            if n < min_len:
                continue
            for i in range(len(text) - n + 1):
                piece = text[i:i + n]
                if piece in known_wrong or piece == term:
                    continue
                if _edit_distance_le1(piece, term):
                    candidates[piece] += 1

    # 合并同一个错写的多种候选，取计数最高的术语
    best: Dict[str, Tuple[str, int]] = {}
    for piece, cnt in candidates.items():
        for term in TERMS:
            if len(term) == len(piece) and _edit_distance_le1(piece, term):
                if piece not in best or cnt > best[piece][1]:
                    best[piece] = (term, cnt)
                break
    return sorted(((p, t, c) for p, (t, c) in best.items()),
                  key=lambda x: -x[2])


def audit_corpus(texts: Iterable[str], top: int = 30) -> None:
    """打印语料里已确认错写的次数 + 疑似错写清单。"""
    joined = "\n".join(t for t in texts if t)
    print("=== 已确认的错写（会被 fix_terms 替换）===")
    if not KNOWN_ERRORS:
        print("  （表为空）")
    for wrong, right in KNOWN_ERRORS.items():
        n = joined.count(wrong)
        print(f"  {wrong} → {right}   {n} 次")
    print("\n=== 疑似错写（只报告，不自动改）===")
    sus = find_suspicious(texts)
    if not sus:
        print("  （无）")
    for piece, term, cnt in sus[:top]:
        print(f"  {piece}（{cnt} 次）  ← 可能应为「{term}」？")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    # 本文件在 zjy5/chibtaici/ 下 → 语料在 ../../zjy7/downloads
    default_src = Path(__file__).resolve().parent.parent.parent / "zjy7" / "downloads"
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else default_src
    if not src.is_dir():
        print(f"❌ 语料目录不存在: {src}")
        sys.exit(2)
    lines = []
    for fp in sorted(src.glob("*.txt")):
        lines.extend(fp.read_text(encoding="utf-8", errors="replace").splitlines())
    print(f"来源: {src}（{len(lines)} 行）\n")
    audit_corpus(lines)
