#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""人设一致性自检：训练 / 部署 / 线上 / 已生成的数据，用的是不是同一份人设。

为什么需要它
------------
人设分布在四处（`persona.py` 有详细说明）：角色卡（线上实际用）、
`prepare_data.py`（训练）、`deploy_ollama.py`（部署 Modelfile）、
以及**已经生成好的 `train_data.json`**。

前三个改一处忘一处，表现是「模型性格怪怪的但说不清哪里怪」；
第四个更隐蔽 —— 训练集是**快照**，换了人设它不会自己更新，
于是你以为在训新人设，其实 1736 条样本里还塞着旧的。
这类问题不看数据根本发现不了，所以做成一条命令。

用法::

    python check_persona.py            # 人可读的报告
    python check_persona.py --json     # 机器可读（给 CI / 脚本用）

退出码::

    0  全部一致
    1  **人设来源**互相不一致（角色卡 / prepare_data / deploy_ollama）—— 必须改代码
    2  人设来源一致，但**生成产物**是旧的（Modelfile / train_data.json）—— 重跑脚本即可

> 两类分开报是因为修法完全不同：前者要改人设本身，后者只是「还没重新生成」。
"""

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

from config_utils import PROJECT_ROOT, resolve_path
from persona import (FALLBACK_SYSTEM, card_metadata, candidate_card_paths,
                     find_card, resolve_system_prompt)


def _load_card_text(card_id="elysia"):
    path = find_card(card_id)
    if not path:
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return None, f"{path}（读取失败：{e}）"
    return (data.get("system_prompt") or "").strip(), str(path)


def _module_system(module_name, attr):
    """导入模块并取它的人设常量（模块导入很轻，不拉 torch）。"""
    try:
        module = __import__(module_name)
        return getattr(module, attr)
    except Exception as e:
        return f"<无法导入 {module_name}: {e}>"


def _modelfile_system(path):
    """从生成的 Modelfile 里抠出 SYSTEM \"\"\"...\"\"\"。"""
    if not Path(path).is_file():
        return None
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    match = re.search(r'SYSTEM\s*"""(.*?)"""', text, re.DOTALL)
    return match.group(1).strip() if match else None


def _train_data_stats(system_text):
    """统计已生成的训练数据里，有多少条用的是当前人设。"""
    out_path = resolve_path("train_data.json")
    if not Path(out_path).is_file():
        return None
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    total = len(data)
    matched = 0
    for sample in data:
        for message in sample.get("messages", []):
            if message.get("role") == "system":
                if (message.get("content") or "").strip() == system_text:
                    matched += 1
                break
    return {"total": total, "matched": matched, "path": str(out_path)}


def _keyword_hits(system_text, words):
    return {word: system_text.count(word) for word in words}


def _first_diff(a, b):
    """给出两段文本第一处不同的上下文（人设很长，全量 diff 没法看）。"""
    if a == b:
        return ""
    lines_a, lines_b = a.splitlines(), b.splitlines()
    for i, (la, lb) in enumerate(zip(lines_a, lines_b), 1):
        if la != lb:
            return f"第 {i} 行不同：\n    A: {la[:60]}\n    B: {lb[:60]}"
    # 行数不同
    diff = list(difflib.unified_diff(lines_a, lines_b, lineterm="", n=0))
    return "\n".join(diff[:6])


def main():
    parser = argparse.ArgumentParser(description="人设一致性自检")
    parser.add_argument("--card", default="elysia", help="角色卡 id（默认 elysia）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    card_text, card_path = _load_card_text(args.card)
    resolved, source = resolve_system_prompt(args.card)

    # 人设来源：这三处必须逐字一致，不一致就是代码/卡片本身有问题
    code_sources = {
        "角色卡（线上实际使用）": card_text,
        "prepare_data.py（训练）": _module_system("prepare_data", "SYSTEM"),
        "deploy_ollama.py（部署）": _module_system("deploy_ollama", "SYSTEM_PROMPT"),
    }
    # 生成产物：不一致只是"还没重新生成"，重跑脚本即可，不是 bug
    artifacts = {
        "Modelfile（已生成）": _modelfile_system(PROJECT_ROOT / "Modelfile"),
    }

    comparable = {k: v for k, v in code_sources.items() if v is not None}
    ok_sources = all(v == resolved for v in comparable.values())

    artifact_status = {
        k: (None if v is None else ("一致" if v == resolved else "过期"))
        for k, v in artifacts.items()
    }
    artifacts_ok = all(s != "过期" for s in artifact_status.values())

    train_stats = _train_data_stats(resolved)
    train_ok = train_stats is None or train_stats["matched"] == train_stats["total"]

    meta = card_metadata(args.card)
    report = {
        "card_path": card_path,
        "resolved_source": source,
        "sources_consistent": ok_sources,
        "sources": {k: ("一致" if v == resolved else "不一致")
                    for k, v in comparable.items()},
        "missing": [k for k, v in code_sources.items() if v is None],
        "artifacts": artifact_status,
        "artifacts_consistent": artifacts_ok,
        "train_data": train_stats,
        "train_data_consistent": train_ok,
        "card_metadata": meta,
        "keyword_hits": _keyword_hits(card_text or "", ["舰长", "第一位", "第二位"]),
        "using_fallback": resolved == FALLBACK_SYSTEM,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if (ok_sources and artifacts_ok and train_ok) else (1 if not ok_sources else 2)

    print("=" * 60)
    print("人设一致性自检")
    print("=" * 60)
    print(f"角色卡   : {card_path or '未找到（搜索路径见下）'}")
    if not card_path:
        for p in candidate_card_paths(args.card):
            print(f"  搜索过 : {p}")
    print(f"解析来源 : {source}")
    print()

    print("人设来源（必须逐字一致）：")
    for name, value in comparable.items():
        flag = "✓ 一致" if value == resolved else "✗ 不一致"
        print(f"  {flag:<10} {name}")
        if value != resolved:
            print("      " + _first_diff(resolved, value or "").replace("\n", "\n      "))
    for name in report["missing"]:
        print(f"  – 不存在   {name}")
    print()

    print("生成产物（不一致 = 还没重新生成）：")
    for name, status in artifact_status.items():
        if status is None:
            print(f"  – 不存在   {name}")
        else:
            print(f"  {'✓ 一致' if status == '一致' else '⚠ 过期':<10} {name}")
    print()

    keywords = report["keyword_hits"]
    print(f"角色卡关键词命中：舰长 ×{keywords.get('舰长', 0)}、"
          f"第一位 ×{keywords.get('第一位', 0)}、第二位 ×{keywords.get('第二位', 0)}")
    if meta.get("user_address"):
        print(f"角色卡规定的称呼：{meta['user_address']}")
    print()

    if train_stats:
        print(f"已生成的训练数据：{train_stats['matched']}/{train_stats['total']} 条"
              f"与当前人设一致")
        if not train_ok:
            print(f"  → 训练集是用**旧人设**生成的（{train_stats['path']}）。")
            print("    换了人设不会自动生效，需要重跑：python prepare_data.py")
    else:
        print("已生成的训练数据：未找到 train_data.json（还没生成过）")
    print()

    if not ok_sources:
        print("❌ 人设来源互相不一致 —— 先修好再训练（见 persona.py 的说明）")
        return 1
    if not artifacts_ok or not train_ok:
        print("⚠️ 人设来源本身一致，但有产物是旧人设生成的：")
        if not artifacts_ok:
            print("     python deploy_ollama.py    # 重新生成 Modelfile")
        if not train_ok:
            print("     python prepare_data.py      # 重新生成 train_data.json")
        return 2

    print("✅ 全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
