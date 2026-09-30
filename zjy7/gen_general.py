#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成**通用指令数据** —— 防止角色微调把基础能力训没了。

【为什么需要】
现在的训练集几乎全是角色数据。纯角色数据微调 2 轮后，模型会「问啥都往角色上拐」：
常识问题、写东西、讲道理——全用爱莉希雅的语气糊过去，**基础指令跟随能力退化**。
这就是灾难性遗忘。

解法是在同一个 LoRA 里混入 10~30% 的**通用指令数据**：让模型在训练时
也持续练习「正常地回答正常的问题」。

【为什么自己生成，不用现成数据集】
现成的中文指令集（如 `AI-ModelScope/alpaca-gpt4-data-zh`）质量更好，
但它的 license 是 **CC BY-NC 4.0（仅非商用）**，且文件名不稳定、要额外下载。
本地生成没有授权问题、不依赖网络，对这个用途（防退化）已经够用。

> 更「忠实」的做法是**回放基座模型自己的输出**（用 `models/base_model`
> 的 Qwen2.5-7B 回答这些问题）—— 那才是真正的 replay。
> 需要额外加载 15GB 模型，暂未做；这里用本地 9B 的回答作为示例。

【用法】

    python gen_general.py --count 200      # 先小批量看质量
    python gen_general.py                  # 默认生成 1000 条
    python gen_general.py --dry-run        # 只看已有多少

输出 `general_data.json`，由 `prepare_data.py` 按比例混入（见 train_config.json
的 `data.general_data_ratio`）。
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config_utils import PROJECT_ROOT  # noqa: E402

DEFAULT_MODEL = "qwen3.5:9b-gguf"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
OUT_PATH = PROJECT_ROOT / "general_data.json"

BATCH = 6                 # 每批几条（问答比单问句长，批小一点）
DEFAULT_COUNT = 1000
PROMPT_VERSION = 1

# 轮流用这些类别，保证覆盖度（只用一个类别会生成一堆同质数据）
CATEGORIES = [
    ("常识问答", "历史、地理、科学、生物、文化方面的常识问题"),
    ("概念解释", "解释一个名词或概念，比如「什么是复利」「什么是光合作用」"),
    ("生活建议", "日常生活的实用建议，比如「怎么去除衣服上的油渍」"),
    ("写作辅助", "帮忙写一小段文字，比如祝福语、道歉的话、简短介绍"),
    ("学习工作", "学习方法、时间管理、职场沟通方面的提问"),
    ("情感人际", "和朋友家人相处的困惑、情绪调节方面的提问"),
    ("推理计算", "需要一点计算或逻辑推理的问题"),
    ("创意点子", "起名字、想点子、策划小活动之类的请求"),
]


def build_prompt(category_desc, n):
    return (
        "你是数据标注员，在为「通用指令数据」构造样本，用来防止模型只会角色扮演。\n\n"
        f"请生成 {n} 条**通用**的问答对，主题方向：{category_desc}。\n\n"
        "要求：\n"
        "1. 问题要像普通用户会问的，**不要涉及任何游戏角色或动漫设定**\n"
        "2. 答案要**准确、有用、简洁**（40~120 字），不要客套话\n"
        "3. 每条的问题和答案都要不一样，覆盖不同的具体话题\n\n"
        f"输出格式：每条一行，序号 + 竖线 + 问题 + 竖线 + 答案。\n"
        "不要 JSON、不要 markdown、不要任何解释。例如：\n"
        "1|什么是复利？|复利是指利息也会产生利息……\n"
        "2|怎么去除衣服上的油渍？|先用纸巾吸掉表面油分……"
    )


def _opener():
    """访问本机 Ollama 必须绕开系统代理。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call_model(prompt, model, url, timeout=240):
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "think": False,     # ⚠️ 必须关思考模式（Qwen3.5 默认开，会吃掉全部 token）
        "options": {"temperature": 0.95, "num_predict": BATCH * 400, "top_p": 0.95},
    }).encode()
    req = urllib.request.Request(url.rstrip("/") + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with _opener().open(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("response", "")


def parse_pairs(raw):
    """解析「序号|问题|答案」行格式。中文内容用行格式比 JSON 稳得多。"""
    text = raw.strip()
    fence = re.search(r"```(?:\w+)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    out = []
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^\s*\d+\s*[.、:：]?\s*[|｜]\s*(.+)$", line)
        if not m:
            continue
        parts = re.split(r"[|｜]", m.group(1))
        if len(parts) < 2:
            continue
        q = parts[0].strip()
        a = "|".join(parts[1:]).strip()      # 答案里可能含竖线，后面的都算答案
        if len(q) >= 4 and len(a) >= 10:
            out.append((q, a))
    return out


def load_existing():
    if OUT_PATH.is_file():
        try:
            d = json.loads(OUT_PATH.read_text(encoding="utf-8"))
            if d.get("meta", {}).get("prompt_version") == PROMPT_VERSION:
                return d
        except Exception:
            pass
    return {"meta": {"prompt_version": PROMPT_VERSION}, "pairs": []}


def save(d):
    d["meta"]["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    d["meta"]["count"] = len(d["pairs"])
    OUT_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="生成通用指令数据（防灾难性遗忘）")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--ollama-url", default=DEFAULT_OLLAMA)
    ap.add_argument("--count", type=int, default=DEFAULT_COUNT, help="目标条数")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    d = load_existing()
    pairs = d["pairs"]
    seen = {q for q, _ in pairs}

    print("=" * 64)
    print("通用指令数据生成")
    print("=" * 64)
    print(f"模型    : {args.model}")
    print(f"已有    : {len(pairs)} 条")
    print(f"目标    : {args.count} 条")

    if args.dry_run:
        print("\n（--dry-run：不实际生成）")
        return 0

    t0 = time.time()
    failed = 0
    cat_i = 0

    while len(pairs) < args.count:
        cat_name, cat_desc = CATEGORIES[cat_i % len(CATEGORIES)]
        cat_i += 1
        try:
            raw = call_model(build_prompt(cat_desc, BATCH), args.model, args.ollama_url)
            got = parse_pairs(raw)
        except Exception as e:
            failed += 1
            print(f"  [{cat_name}] ❌ {type(e).__name__}: {str(e)[:50]}")
            if failed >= 10:
                print("  连续失败过多，中止")
                break
            continue

        new = 0
        for q, a in got:
            if q in seen:
                continue
            seen.add(q)
            pairs.append([q, a])
            new += 1
        save(d)

        el = time.time() - t0
        rate = len(pairs) / el if el else 0
        eta = (args.count - len(pairs)) / rate / 60 if rate else 0
        print(f"  [{cat_name}] +{new}  累计 {len(pairs)}/{args.count}"
              f"  ({len(pairs)/args.count*100:.0f}%)  {rate:.2f} 条/s"
              f"  剩余 {eta:.1f} 分钟")

    el = time.time() - t0
    print()
    print("=" * 64)
    print(f"完成：共 {len(pairs)} 条，耗时 {el/60:.1f} 分钟")
    print(f"输出：{OUT_PATH}")
    if failed:
        print(f"⚠️ {failed} 个批次失败")
    return 0


if __name__ == "__main__":
    sys.exit(main())
