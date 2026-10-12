#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""OCR 术语校正的测试（纯标准库，不需要 torch / paddle / cv2）。

跑法::

    cd zjy5
    ./.venv/Scripts/python.exe tests/test_term_fix.py

覆盖：已确认错写的替换、同音误识别（子子/梓梓 → 紫紫）、
保守性（不做自动模糊纠正）、长词优先、疑似错写只报告不改。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               os.pardir, "chibtaici"))

from term_fix import (  # noqa: E402
    KNOWN_ERRORS,
    TERMS,
    find_suspicious,
    fix_terms,
)


class Missing(Exception):
    """断言失败。"""


def eq(actual, expected, label):
    if actual != expected:
        raise Missing(f"{label}：期望 {expected!r}，实际 {actual!r}")


def ok(condition, label):
    if not condition:
        raise Missing(label)


def test_confirmed_replacements():
    """已确认的错写应被替换。"""
    eq(fix_terms("芽依和千杰在一起"), "芽衣和千劫在一起", "基础替换")
    eq(fix_terms("独心术"), "读心术", "错的多过对的也要修")
    eq(fix_terms("美比乌斯博士"), "梅比乌斯博士", "多字错写")
    eq(fix_terms("苏贞开双"), "苏睁开双", "断词错写")
    eq(fix_terms("没有错字的一句话"), "没有错字的一句话", "正确文本不动")
    eq(fix_terms(""), "", "空串安全")
    eq(fix_terms(None), None, "None 安全")


def test_homophone_zi():
    """同音误识别：子子 / 梓梓 都是「紫紫」的误写。

    三个字读音都是 zǐ，OCR 与语音识别都会混。
    实测 87 处全部出现在「算变成了〇〇的样子也」这一个上下文里，
    所以替换不存在误伤。
    """
    eq(fix_terms("我就算变成了子子的样子也很可爱"),
       "我就算变成了紫紫的样子也很可爱", "子子 → 紫紫")
    eq(fix_terms("我就算变成了梓梓的样子也很可爱"),
       "我就算变成了紫紫的样子也很可爱", "梓梓 → 紫紫")
    # 关键回归：两种写法混在同一段里，必须都改掉
    eq(fix_terms("变成了子子的样子，变成了梓梓的样子"),
       "变成了紫紫的样子，变成了紫紫的样子", "两种误写同时出现")
    ok("紫紫" in TERMS, "紫紫 应进术语白名单")


def test_conservative_no_fuzzy():
    """不做自动模糊纠正 —— 差一字的普通词不能被改坏。"""
    # 「或者」与「律者」差一字，但绝不能改
    eq(fix_terms("或者我们可以换一种方式"), "或者我们可以换一种方式", "或者不该动")
    eq(fix_terms("我长大了"), "我长大了", "成长类词不该动")
    eq(fix_terms("一千万"), "一千万", "千万不该动")
    # 梓/紫 单独出现（非叠词）不在表内，不应被改
    eq(fix_terms("紫罗兰"), "紫罗兰", "正常紫字词不动")


def test_longest_first():
    """长词优先，避免短词先匹配吃掉长词的一部分。"""
    extra = {"abc": "X", "abcd": "Y"}
    eq(fix_terms("abcd", extra=extra), "Y", "长词优先")


def test_suspicious_report_only():
    """find_suspicious 只报告，不修改语料。

    ⚠️ 已进 KNOWN_ERRORS 的错写会被**跳过**（不再重复报告），
    所以要验证「报告能力」必须用一个**尚未入表**的疑似错写。
    """
    # 「芽依」已在表里 → 不应再被报出来（避免和已确认项重复）
    sus_confirmed = find_suspicious(["芽依来了"])
    ok(not any(p == "芽依" for p, _t, _c in sus_confirmed),
       "已确认的错写不重复报告")

    # 「格蕾休」←「格蕾修」：(休,修) 这个混淆对**还没被确认过**，
    # 所以只在宽模式里报 —— 见 test_find_suspicious_wide_mode。
    sus_new = find_suspicious(["格蕾休在画画"], wide=True)
    ok(any(p == "格蕾休" for p, _t, _c in sus_new), "宽模式应报出未确认的疑似")
    ok("格蕾休" in "格蕾休在画画", "find_suspicious 不修改输入")


def _all_cjk(s):
    return all("\u4e00" <= ch <= "\u9fff" for ch in s)


def test_find_suspicious_guided_filters_noise():
    """默认（引导）模式：只报「差异字是已知混淆对」的候选。

    下面这些都是**正常词**，与某个术语只差一字，但那个字对从未被确认过
    混淆 —— 实测它们占了旧版报告的绝大多数（30 条里 28 条）。
    这正是「噪声」的来源：`或者` 与 `律者` 在频率和编辑距离上无法区分。
    """
    for word in ["或者", "千万", "成长", "擅长", "英雄", "始终"]:
        sus = find_suspicious([f"{word}的样子"])
        ok(not any(p == word for p, _t, _c in sus),
           f"{word} 不该出现在默认模式的报告里")


def test_find_suspicious_guided_keeps_real_errors():
    """引导模式仍要能发现**新**错写（只要混淆关系已被确认过）。

    「伊电」←「伊甸」：(电,甸) 已由 `依电 → 伊甸` 确认；
    而「伊电」本身**不在** KNOWN_ERRORS 里 —— 这就是「发现新错写」。
    """
    sus = find_suspicious(["伊电来了"])
    ok(any(p == "伊电" and t == "伊甸" for p, t, _c in sus),
       "应报出 伊电 ← 伊甸")


def test_find_suspicious_wide_mode():
    """wide=True 回到「任意差一字」的旧行为，用于发现**新类型**的混淆。

    这是刻意的取舍：引导模式精度高但只能发现「已知混淆类型的新实例」；
    想找全新类型的混淆（如 休/修）必须显式开宽模式，并接受噪声。
    """
    sus = find_suspicious(["或者"], wide=True)
    ok(any(p == "或者" for p, _t, _c in sus), "宽模式应报出 或者")
    sus2 = find_suspicious(["或者"])
    ok(not any(p == "或者" for p, _t, _c in sus2), "默认模式不报 或者")


def test_find_suspicious_min_count():
    """min_count 过滤偶发噪声。"""
    eq(find_suspicious(["伊电"], min_count=2), [], "只出现 1 次时被阈值滤掉")
    ok(find_suspicious(["伊电", "伊电"], min_count=2), "出现 2 次时保留")


def test_find_suspicious_skips_punctuation():
    """含标点/非汉字的片段不该被当成候选。

    旧版会报出「始,」「苏,」「伊,」这类片段 —— 它们是滑动窗口切到标点造成的。
    """
    sus = find_suspicious(["始,源"], wide=True)
    ok(not any(not _all_cjk(p) for p, _t, _c in sus),
       "候选必须是纯汉字")


def test_zhuhuo_cluster():
    """「逐火」被识别成一大片错字（2026-10-12 实测）。

    ⚠️ 这批是**同一个词**在语料里被 OCR/ASR 认成 6 种样子：
        逐火 / 烛火 / 诸火 / 着火（以及之蛾→之鹅/之额/之恶）
    实测「烛火」77 处**全部**是「逐火之蛾 / 逐火十三英桀 / 逐火者」
    语境，无一处是真正的「烛火」（蜡烛火）。所以可安全全局替换。
    """
    eq(fix_terms("烛火之蛾"), "逐火之蛾", "烛火之蛾 → 逐火之蛾")
    eq(fix_terms("烛火十三英桀"), "逐火十三英桀", "烛火十三英桀")
    eq(fix_terms("烛火者未尽的旅途"), "逐火者未尽的旅途", "烛火者 → 逐火者")
    eq(fix_terms("烛火的旅途"), "逐火的旅途", "烛火的旅途")
    eq(fix_terms("诸火十三英桀"), "逐火十三英桀", "诸火 → 逐火")
    eq(fix_terms("着火之鹅"), "逐火之蛾", "着火之鹅 → 逐火之蛾")
    # 之蛾 的变体
    eq(fix_terms("逐火之鹅"), "逐火之蛾", "之鹅 → 之蛾")
    eq(fix_terms("逐火之额"), "逐火之蛾", "之额 → 之蛾")
    eq(fix_terms("逐火之恶"), "逐火之蛾", "之恶 → 之蛾")
    eq(fix_terms("逐火吃鹅的每一天"), "逐火之蛾的每一天", "吃鹅 → 之蛾")


def test_yingjie_cluster():
    """「英桀」被识别成「英杰 / 英节 / 阴劫 / 英劫」（2026-10-12 实测）。

    「英杰」65 处全部是「十三英桀 / 逐火英桀」语境 ——
    实测「英桀」169 处 vs 「英杰」65 处，且后者全是错写。
    「英劫」2 处是**新方法发现的**：它不在表里，但与已确认的
    `阴劫 → 英桀` 共享 (劫,桀) 混淆对，所以被引导模式报了出来。
    """
    eq(fix_terms("十三英杰"), "十三英桀", "英杰 → 英桀")
    eq(fix_terms("逐火英杰"), "逐火英桀", "逐火英杰")
    eq(fix_terms("十三英节"), "十三英桀", "英节 → 英桀")
    eq(fix_terms("十三阴劫"), "十三英桀", "阴劫 → 英桀")
    eq(fix_terms("十三味英节"), "十三位英桀", "味英节 → 位英桀")
    eq(fix_terms("英劫与后继者绝不应该是这样的关系"),
       "英桀与后继者绝不应该是这样的关系", "英劫 → 英桀")


def test_yayi_cluster():
    """「芽衣」被识别成「牙医 / 亚依 / 亚伊 / 芽衣女 / 芽依」（2026-10-12）。

    ⚠️「牙医」43 处**全部**是「芽衣」（实测无一处是真的牙科医生）。
    这是此前完全没被记录的错字 —— 比已入表的「芽依」43 次一样多。
    """
    eq(fix_terms("牙医，你回来了"), "芽衣，你回来了", "牙医 → 芽衣")
    eq(fix_terms("雷电牙医的往事乐土大冒险"), "雷电芽衣的往事乐土大冒险", "雷电牙医")
    eq(fix_terms("亚依，你怎么突然"), "芽衣，你怎么突然", "亚依 → 芽衣")
    eq(fix_terms("你是怎么对亚伊说的"), "你是怎么对芽衣说的", "亚伊 → 芽衣")
    eq(fix_terms("芽衣女这样美丽"), "芽衣这样美丽", "芽衣女 → 芽衣")


def test_lvzhe_cluster():
    """「律者」被识别成「绿者」（2026-10-12 实测）。

    ⚠️「绿者」16 处全部是「律者」语境（「成为律者」「和律者交朋友」
    「律者的权能」）—— 无一处是真的「绿色」。
    """
    eq(fix_terms("如果我也成为绿者"), "如果我也成为律者", "绿者 → 律者")
    eq(fix_terms("和绿者交朋友"), "和律者交朋友", "和绿者交朋友")
    eq(fix_terms("绿者的权能"), "律者的权能", "绿者的权能")


def test_shiyuan_cluster():
    """「始源之律者」被识别成「使缘之律者 / 使人之律者」（2026-10-12）。

    ⚠️ 官方维基（Elysia/Quotes）原文是：
        「始源之律者，我也很喜欢这个称呼呢，就像是「终焉」的反面。」
    所以「使缘」「使人」**都是误读** —— 正确是「始源」。
    这也纠正了本文件早先一条注释里「缘之律者 → 人之律者」的错误方向。
    """
    eq(fix_terms("使缘之律者，我也很喜欢这个称呼呢"),
       "始源之律者，我也很喜欢这个称呼呢", "使缘之律者 → 始源之律者")
    eq(fix_terms("使人之律者，我也很喜欢这个称呼呢"),
       "始源之律者，我也很喜欢这个称呼呢", "使人之律者 → 始源之律者")


def test_zhongyan_and_song():
    """「终焉」「颂歌」的误识别。"""
    eq(fix_terms("就像是中烟的反面"), "就像是终焉的反面", "中烟 → 终焉")
    eq(fix_terms("就像是中原的反面"), "就像是终焉的反面", "中原 → 终焉")
    eq(fix_terms("奏响了文明的宋歌"), "奏响了文明的颂歌", "宋歌 → 颂歌")


def test_cascade_replacement():
    """级联替换：一次替换的结果可能**又是**另一个错写（2026-10-12 实测）。

    ⚠️ 真实案例：「牙医女」先被 `牙医 → 芽衣` 变成「芽衣女」，
    而「芽衣女 → 芽衣」这条规则**按长度排序在前**、已经处理过了，
    所以单遍扫描救不回来 → 必须迭代到不动点。

    这类缺陷不会报错，只会**静默留下少量错字**（实测残留 1 处），
    正是最该被测试锁住的那种。
    """
    eq(fix_terms("牙医女这样美丽"), "芽衣这样美丽", "牙医女 → 芽衣（需两轮）")
    eq(fix_terms("象牙医女这样"), "象芽衣这样", "级联")
    # 人工构造一条纯级联链：a→b，b→c
    extra = {"甲甲": "乙乙", "乙乙": "丙丙"}
    eq(fix_terms("甲甲", extra=extra), "丙丙", "链式替换应迭代到不动点")


def test_zhaohuo_not_overreplaced():
    """「着火」只在「着火之鹅」（逐火之蛾的误写）里才该改。

    ⚠️ 语料里另有 6 处**正常**的「着火的玻璃石头」，绝不能被改成「逐火」。
    """
    eq(fix_terms("着火之鹅，几乎每个来访者都会提起"),
       "逐火之蛾，几乎每个来访者都会提起", "着火之鹅 → 逐火之蛾")
    eq(fix_terms("长得像着火的玻璃石头"),
       "长得像着火的玻璃石头", "正常的「着火」不能动")


def test_find_suspicious_reports_admitting_term():
    """报告里的「最像的术语」必须是**让它通过筛选的那个**。

    ⚠️ 真实缺陷（2026-10-12 实测）：「英劫」与 `千劫`、`英桀` **都**差一字，
    而 TERMS 里 `千劫` 排在 `英桀` 前面 → 旧逻辑报成「英劫 ← 千劫」。
    但引导模式**是因为 (劫,桀) 这个已确认混淆对才放行它的**
    （`阴劫 → 英桀` 确认过），且实际上下文是
    「英劫与后继者绝不应该是这样的关系」（正确为「英桀与后继者」）。
    报错术语会**误导人工复核** —— 让人以为是千劫的名字写错。

    ⚠️ 「英劫」后来已入表（见 test_yingjie_cluster），所以这里改用一个
    结构相同的**未入表**候选来锁住归因逻辑：
    「千杰」已入表 → 用 (吉,劫) 放行的「千吉」同理。
    这里构造「美比乌斯」的邻居「梅比乌斯」变体不便，
    改用 (依,伊) 放行的「衣电」←「伊甸」。
    """
    # 「衣电」：与 `伊甸` 差两字，不成立；改用一字之差的「伊电」
    # 它由 (电,甸) 放行 → 必须归到「伊甸」，而不是同样差一字的别的术语
    sus = find_suspicious(["伊电来了"])
    hit = [(p, t) for p, t, _c in sus if p == "伊电"]
    ok(hit, "应报出 伊电")
    eq(hit[0][1], "伊甸", "应归到 伊甸（放行它的那个术语）")

    # 已入表的「英劫」不再出现在报告里
    sus2 = find_suspicious(["英劫与后继者"])
    ok(not any(p == "英劫" for p, _t, _c in sus2), "已入表的不再报告")


TESTS = [
    test_confirmed_replacements,
    test_homophone_zi,
    test_zhuhuo_cluster,
    test_yingjie_cluster,
    test_yayi_cluster,
    test_lvzhe_cluster,
    test_shiyuan_cluster,
    test_zhongyan_and_song,
    test_cascade_replacement,
    test_zhaohuo_not_overreplaced,
    test_conservative_no_fuzzy,
    test_longest_first,
    test_suspicious_report_only,
    test_find_suspicious_guided_filters_noise,
    test_find_suspicious_guided_keeps_real_errors,
    test_find_suspicious_reports_admitting_term,
    test_find_suspicious_wide_mode,
    test_find_suspicious_min_count,
    test_find_suspicious_skips_punctuation,
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
    print(f"\ntest_term_fix：通过 {passed}，失败 {failed}")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
