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

2026-10-12 全量复查又挖出**成片的**错字（此前完全没记录），
共 576 处、30 条规则，其中最大的一批是同一个词被认成多种样子：

| 正确       | 错写（次数） |
|-----------|-------------|
| 逐火       | 烛火 77 / 诸火 2 / 着火之鹅 2 |
| 之蛾       | 之鹅 26 / 之额 12 / 之恶 14 / 吃鹅 2 |
| 英桀       | 英杰 65 / 英节 4 / 阴劫 6 / 味英节 2 |
| 芽衣       | **牙医 43** / 亚依 10 / 亚伊 4 / 芽衣女 1 |
| 律者       | 绿者 16 |
| 始源之律者 | 使缘之律者 18 / 使人之律者 8 |
| 终焉       | 中烟 20 / 中原 12 |
| 紫紫       | 子子 38 / 梓梓 24 |

> ⚠️ **「牙医」43 处实测全是「芽衣」**，无一处是真的牙科医生 ——
> 数量和早已入表的「芽依」一样多，却一直没被发现。

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
⚠️ 但 2 字术语的误报率极高（实测 30 条报告里真错字只有 2 条，
「或者→律者」「成长→舰长」全是误报）——**必须逐条看上下文**，
像「烛火」「牙医」那样统计出「零例外」才敢入表。
"""
import re
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
    # 爱莉希雅的玩梗台词用语：「我就算变成了紫紫的样子也很可爱」
    # 见 KNOWN_ERRORS 里 子子/梓梓 两条 —— 两者都是它的同音误写。
    "紫紫",
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
    # ---- 同音误识别（2026-10-12 查证）----
    # 「我就算变成了〇〇的样子也很可爱，你也这么觉得，对吧？」
    # 子 / 梓 / 紫 **读音都是 zǐ**，OCR 与语音识别都会混。
    # 官方维基（honkaiimpact3.fandom.com/wiki/Elysia/Quotes）作「紫紫」，
    # 另一处台词整理站独立记录一致 —— 所以**两个误写都指向紫紫**，
    # 不是「子子 与 梓梓 二选一」。
    # 实测 87 处（子子 19+33 / 梓梓 32）**全部**落在
    # 「算变成了〇〇的样子也」这一个上下文里，替换无误伤。
    "子子": "紫紫",         # 19 次（另 33 次在 speaker_filtered 产物中）
    "梓梓": "紫紫",         # 12 次

    # ---- 逐火之蛾 / 逐火十三英桀（2026-10-12 全量实测）----
    # ⚠️ 同一个词在语料里被认成 **6 种**样子：逐火 / 烛火 / 诸火 / 着火。
    # 「烛火」77 处实测**全部**是「逐火之蛾 / 逐火十三英桀 / 逐火者」语境，
    # 无一处是真正的「烛火」（蜡烛火）—— 所以可安全全局替换。
    # 「之蛾」的变体同样成片：「之鹅」26 / 「之额」12 / 「之恶」14 / 「吃鹅」2，
    # 而正确的「之蛾」有 112 处。
    "烛火": "逐火",         # 77 次（之鹅/之额/之恶/吃鹅 都跟在它后面）
    "诸火": "逐火",         # 2 次
    # ⚠️ 只改「着火之蛾」这一个搭配：语料里另有 6 处**正常**的
    #    「着火的玻璃石头」，不能一起改（实测「着火」共 8 处，其中 2 处是错写）。
    "着火之鹅": "逐火之蛾",   # 2 次
    "之鹅": "之蛾",         # 26 次
    "之额": "之蛾",         # 12 次
    "之恶": "之蛾",         # 14 次
    "吃鹅": "之蛾",         # 2 次（「烛火吃鹅的每一天」）

    # ---- 英桀（2026-10-12 全量实测）----
    # 「英桀」正确 169 处 vs 错写「英杰」65 处；后者全是
    # 「十三英杰 / 逐火英杰 / 英杰们」语境，无一处是「英雄豪杰」义。
    "英杰": "英桀",         # 65 次
    "英节": "英桀",         # 4 次
    "阴劫": "英桀",         # 6 次（「十三阴劫」「烛火十三阴劫」）
    # 「英劫」2 次 —— 上下文「英劫与后继者绝不应该是这样的关系」。
    # 它是**引导模式找出来的**：与 `阴劫 → 英桀` 共享 (劫,桀) 混淆对。
    "英劫": "英桀",
    "味英节": "位英桀",       # 2 次（「十三味英节」←「十三位英桀」，味=位）

    # ---- 芽衣（2026-10-12 全量实测）----
    # ⚠️ 这是此前**完全没被记录**的一批：「牙医」43 处实测全是「芽衣」，
    # 无一处是真的牙科医生（对比：已入表的「芽依」也是 43 次）。
    "牙医": "芽衣",         # 43 次
    "亚依": "芽衣",         # 10 次
    "亚伊": "芽衣",         # 4 次
    "芽衣女": "芽衣",        # 1 次（「芽衣女这样美丽」）

    # ---- 律者 / 始源 / 终焉 / 颂歌（2026-10-12）----
    # 「绿者」16 处全是「律者」语境（成为律者 / 和律者交朋友 / 律者的权能），
    # 无一处是真的「绿色」。
    "绿者": "律者",         # 16 次
    # ⚠️ 官方维基原文：「始源之律者，我也很喜欢这个称呼呢，就像是「终焉」的反面。」
    # 所以「使缘」「使人」**都是误读**，正确是「始源」。
    # （本文件早先注释里写的「缘之律者 → 人之律者」方向是错的，已纠正。）
    "使缘之律者": "始源之律者",   # 18 次
    "使人之律者": "始源之律者",   # 8 次
    "中烟的反面": "终焉的反面",   # 20 次
    "中原的反面": "终焉的反面",   # 12 次
    "宋歌": "颂歌",         # 4 次（「奏响了文明的宋歌」）

    # ---- 以下为「疑似」但**未经确认**，暂不启用 ----
}

# 把长词放前面，避免短词先匹配吃掉长词的一部分
_SORTED = sorted(KNOWN_ERRORS.items(), key=lambda kv: -len(kv[0]))


def fix_terms(text: str, extra: Dict[str, str] = None) -> str:
    """按已确认的错写表替换。没有把握的**不猜**。

    ⚠️ **必须迭代到不动点，不能只扫一遍**（2026-10-12 修）：
    一次替换的结果可能**又是**另一个错写。真实案例 ——「牙医女」：
    第一轮 `牙医 → 芽衣` 得到「芽衣女」，而「芽衣女 → 芽衣」这条规则
    **按长度排序在前、已经处理过了**，单遍扫描就漏掉它。
    这类缺陷不报错、只静默残留少量错字（实测残留 1 处），最难发现。

    迭代上限 10 轮：正常表最多两三轮就收敛；万一有人加出循环规则
    （`a→b` 同时 `b→a`），上限能保证不挂死。
    """
    if not text:
        return text
    table = _SORTED if not extra else sorted(
        {**KNOWN_ERRORS, **extra}.items(), key=lambda kv: -len(kv[0]))
    for _ in range(10):
        before = text
        for wrong, right in table:
            if wrong in text:
                text = text.replace(wrong, right)
        if text == before:          # 不动点：再跑也不会变
            break
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


def _all_cjk(s: str) -> bool:
    """全是汉字（滑动窗口会切出带标点的片段，那些不是候选）。"""
    return bool(s) and all("\u4e00" <= ch <= "\u9fff" for ch in s)


def _confusion_pairs() -> set:
    """从 `KNOWN_ERRORS` 反推「已确认的错字 → 正字」混淆对。

    这是本模块发现**新**错写的唯一依据。为什么不查拼音：
    本机没装 `pypinyin`（PyPI 也不可达），而本模块的设计约束是
    **纯标准库、能离线跑**（见 README「采集端的四条硬要求」）。

    做法很直接：人工确认过的替换里，逐位比对就能拿到「哪些字会互认错」——
    `依电 → 伊甸` 给出 (依,伊) 和 (电,甸)，于是「伊电」这种**没见过**的
    错写也能被发现。等于用已确认的知识去引导搜索。

    只取**等长**的条目：不等长的是增删（如 `芽衣女 → 芽衣`），
    无法逐位配对。
    """
    pairs = set()
    for wrong, right in KNOWN_ERRORS.items():
        if len(wrong) != len(right):
            continue
        for a, b in zip(wrong, right):
            if a != b:
                pairs.add((a, b))
    return pairs


def _diff_in_pairs(piece: str, term: str, pairs: set) -> bool:
    """等长，且每个差异位置的字对都在已确认的混淆对里。"""
    if len(piece) != len(term):
        return False
    return all((a, b) in pairs for a, b in zip(piece, term) if a != b)


def find_suspicious(texts: Iterable[str], min_len: int = 2,
                    wide: bool = False, min_count: int = 1) -> List[Tuple[str, str, int]]:
    """找出「和术语只差一个字」的片段，返回 [(疑似错写, 最像的术语, 次数)]。

    ⚠️ 只报告不修改 —— 差一个字可能是错写，也可能是个完全正常的词。
    人工确认之后再加进 `KNOWN_ERRORS`。

    两种模式（2026-10-12 加，为治噪声）：

    - **默认（引导模式）**：只报「差异字是**已确认混淆对**」的候选。
      精度高得多 —— 实测把 `或者`(→律者) / `成长`(→舰长) / `千万`(→千劫)
      这类误报全部滤掉，因为「或→律」「成→舰」「万→劫」从未被确认过。
      代价：只能发现**已知混淆类型的新实例**。
    - **`wide=True`（宽模式）**：回到「任意差一字」的旧行为。
      用来发现**全新类型**的混淆（如 休/修），但必须人工逐条看 ——
      实测旧行为 30 条报告里真错字只有 2 条。

    这就是「精度 vs 召回」的取舍：日常用引导模式，
    想找新类型时显式开宽模式并接受噪声。
    """
    known_wrong = set(KNOWN_ERRORS)
    pairs = None if wide else _confusion_pairs()
    # piece -> (术语, 次数)：**记下让它通过筛选的那个术语**。
    # 不能事后拿 TERMS 重扫一遍再取第一个命中的 ——
    # 「英劫」与 `千劫`、`英桀` 都差一字，而 TERMS 里 `千劫` 在前，
    # 就会误报成「英劫 ← 千劫」，误导人工复核。
    found: Dict[str, Tuple[str, int]] = {}
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
                if not _all_cjk(piece):
                    continue
                if not _edit_distance_le1(piece, term):
                    continue
                if pairs is not None and not _diff_in_pairs(piece, term, pairs):
                    continue
                cnt = found[piece][1] + 1 if piece in found else 1
                found[piece] = (term, cnt)

    result = [(p, t, c) for p, (t, c) in found.items() if c >= min_count]
    return sorted(result, key=lambda x: -x[2])


def audit_corpus(texts: Iterable[str], top: int = 30, wide: bool = False) -> None:
    """打印语料里已确认错写的次数 + 疑似错写清单。

    `wide=False`（默认）只看「已知混淆类型的新实例」，报告很干净；
    `wide=True` 回到「任意差一字」，用来找**全新类型**的混淆（噪声大）。
    """
    joined = "\n".join(t for t in texts if t)
    print("=== 已确认的错写（会被 fix_terms 替换）===")
    if not KNOWN_ERRORS:
        print("  （表为空）")
    for wrong, right in KNOWN_ERRORS.items():
        n = joined.count(wrong)
        print(f"  {wrong} → {right}   {n} 次")
    mode = "宽模式（任意差一字，噪声大）" if wide else "引导模式（只报已知混淆类型）"
    print(f"\n=== 疑似错写（只报告，不自动改）· {mode} ===")
    sus = find_suspicious(texts, wide=wide)
    if not sus:
        print("  （无）")
    for piece, term, cnt in sus[:top]:
        print(f"  {piece}（{cnt} 次）  ← 可能应为「{term}」？")
    if not wide and not sus:
        print("  → 想找**全新类型**的混淆，加 --wide 重跑（会有一堆误报，需人工看）")


if __name__ == "__main__":
    import sys
    from pathlib import Path
    argv = [a for a in sys.argv[1:] if a != "--wide"]
    wide = "--wide" in sys.argv[1:]
    # 本文件在 zjy5/chibtaici/ 下 → 语料在 ../../zjy7/downloads
    default_src = Path(__file__).resolve().parent.parent.parent / "zjy7" / "downloads"
    src = Path(argv[0]) if argv else default_src
    if not src.is_dir():
        print(f"❌ 语料目录不存在: {src}")
        sys.exit(2)
    lines = []
    for fp in sorted(src.glob("*.txt")):
        lines.extend(fp.read_text(encoding="utf-8", errors="replace").splitlines())
    print(f"来源: {src}（{len(lines)} 行）\n")
    audit_corpus(lines, wide=wide)
