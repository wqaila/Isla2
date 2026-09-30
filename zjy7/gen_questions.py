#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给每条台词生成**多样化的用户提问** —— 治「提问坍缩」的根。

【为什么需要这个】
`prepare_data.py` 的 `mono_samples()` 把上千句台词按关键词映射到
**约 40 个模板问句**上（MONO_RULES 的 27 条 + OTHER_CHARS 的 13 个「说说X吧」）。
结果是 **80% 的训练样本只用到了这 40 个问句** —— 模型对这几个问法特别顺，
换个说法就发懵。

「削峰」（把高频问句压到几十条）只是治标：那等于**删掉**几百条样本，
模型少见了同一个问法，但**依然没见过其他问法**。
真正的解法是给每条台词生成几个**不同的**问法，让问法种类从 40 涨到几千。

【用法】

    # 先小批量试跑，看看质量
    python gen_questions.py --limit 30

    # 全量（约 1724 句，实测约 15 分钟）
    python gen_questions.py

    # 只统计还差多少，不实际生成
    python gen_questions.py --dry-run

【设计取舍】
- **结果落 `question_cache.json`，每批存一次** —— 中断后重跑只补缺的，
  不会白跑（1724 句要跑 15 分钟，不能丢）。
- **缓存按台词原文做 key**（不是哈希）—— 可读、可人工修正、
  重排顺序也不影响命中。
- **只接受落在本批范围内的序号**：实测模型偶尔会多返回一项
  （给 10 句返回 11 项），不校验就会把别的台词的问题错配过来。
- **失败不中断**：某批解析不了就记下来，最后汇总重试一次；
  仍然失败的那些留给下次运行（缓存机制天然支持）。
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config_utils import PROJECT_ROOT, load_config, resolve_path  # noqa: E402

# 实测：这个模型在本机 100% 跑在显存上，96 tok/s
DEFAULT_MODEL = "qwen3.5:9b-gguf"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
CACHE_PATH = PROJECT_ROOT / "question_cache.json"

BATCH_SIZE = 10           # 每批几句（实测 10 句 ≈ 5.1s）
QUESTIONS_PER_LINE = 3    # 每句要几个问法
PROMPT_VERSION = 2        # 改了下面的 prompt 就 +1，便于识别旧缓存

# 质量红线：这些词说明模型在问「台词本身」，而不是在跟角色聊天
_META_WORDS = ("台词", "这句话", "问法", "上文", "刚才说")
MIN_Q_LEN, MAX_Q_LEN = 4, 40


def build_prompt(lines):
    body = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(lines))
    return (
        "你是数据标注员，正在为角色扮演模型的训练集构造「用户提问」。\n\n"
        "**背景**：下面每一句都是**角色（爱莉希雅）说的话**。"
        "你要**倒推**——用户问了什么，她才会这样回答？\n\n"
        "要求：\n"
        "1. 提问必须是**用户（玩家）对角色说的话**，不是角色说的话\n"
        "2. 台词是**回答**，你的提问要能自然地引出这个回答\n"
        "3. 问法要多样：不同句式、不同切入角度\n"
        "4. 不要出现「台词」「这句话」「回答」这些词\n"
        f"5. 每个提问 {MIN_Q_LEN}~{MAX_Q_LEN} 字，口语化\n\n"
        f"台词：\n{body}\n\n"
        f"输出格式：每句一行，序号 + 竖线 + {QUESTIONS_PER_LINE} 个问法（也用竖线分隔）。\n"
        "不要 JSON、不要 markdown、不要任何解释。例如：\n"
        f"1|问法一|问法二|问法三\n2|问法一|问法二|问法三"
    )


def _opener():
    """访问本机 Ollama 必须绕开系统代理（否则代理会把 127.0.0.1 也接走 → 502）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call_model(prompt, model, ollama_url, batch_size, timeout=180):
    """调一次 Ollama，返回原始文本。"""
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        # ⚠️ 必须显式关掉思考模式：Qwen3.5 默认会把 token 全花在
        #    "Thinking Process:" 上，正文根本轮不到。
        #    prompt 里加 /no_think 是**无效**的，只有这个参数管用。
        "think": False,
        "options": {
            "temperature": 0.9,      # 要多样性
            "num_predict": batch_size * 120,
            "top_p": 0.95,
        },
    }).encode()
    req = urllib.request.Request(ollama_url.rstrip("/") + "/api/generate",
                                 data=body,
                                 headers={"Content-Type": "application/json"})
    with _opener().open(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("response", "")


def parse_questions(raw, n_lines):
    """从模型输出里解析出 {行号(0-based): [问法,...]}。

    ⚠️ 为什么不用 JSON：实测模型输出**中文内容的 JSON 经常非法**
    （转义、全角标点、少逗号都会炸），而且报错位置飘忽、难修。
    改成「序号|问法|问法」的行格式后，解析变成纯字符串切分，
    对中文完全免疫。

    容错点：
    - 可能带 markdown 代码块围栏 → 去掉
    - 可能用全角竖线 `｜` 或逗号 → 都接受
    - **按 n_lines 校验序号**：实测模型会多返回一项，
      不校验就会把别的台词的问题错配过来
    """
    text = raw.strip()
    fence = re.search(r"```(?:\w+)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # 允许 "1|a|b|c" / "1｜a｜b" / "1. a|b" 等
        m = re.match(r"^\s*(\d+)\s*[.、:：]?\s*[|｜]\s*(.+)$", line)
        if not m:
            continue
        idx = int(m.group(1)) - 1
        if not (0 <= idx < n_lines):     # ⚠️ 关键：挡住越界项
            continue
        parts = re.split(r"[|｜]", m.group(2))
        out[idx] = [p.strip() for p in parts if p.strip()]
    return out


def clean_questions(qs, line):
    """过滤问法：去空白、卡长度、去元问题、去重、去掉和台词重复的。"""
    cleaned, seen = [], set()
    for q in qs:
        q = re.sub(r"\s+", "", str(q)).strip("　 ")
        if not (MIN_Q_LEN <= len(q) <= MAX_Q_LEN):
            continue
        if any(w in q for w in _META_WORDS):
            continue
        if q == line or q in seen:
            continue
        seen.add(q)
        cleaned.append(q)
    return cleaned[:QUESTIONS_PER_LINE]


def collect_lines():
    """取出需要生成提问的台词（与 prepare_data 的 mono 路线保持一致）。"""
    from prepare_data import MONO_RULES, OTHER_CHARS, parse_dialogue, split_long_text

    cfg = load_config()
    raw_dir = resolve_path(cfg["data"]["raw_data_dir"])

    lines, seen = [], set()
    for fp in sorted(raw_dir.glob("*.txt")):
        ds = parse_dialogue(fp)
        roles = {d["role"] for d in ds if d["role"] != "旁白"}
        if len(roles) >= 2:
            continue                     # 对话型文件走 dialogue 路线，不需要生成问法
        for d in ds:
            for piece in split_long_text(d["content"]):
                if piece in seen:
                    continue
                # 只保留「当前会被套上模板问句」的那些
                if any(w in piece for kws, _, _ in MONO_RULES for w in kws) \
                        or any(c in piece for c in OTHER_CHARS):
                    seen.add(piece)
                    lines.append(piece)
    return lines


def load_cache():
    if CACHE_PATH.is_file():
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if data.get("meta", {}).get("prompt_version") == PROMPT_VERSION:
                return data
            print(f"⚠️ 缓存是旧 prompt（v{data.get('meta', {}).get('prompt_version')}）"
                  f"生成的，忽略重来")
        except Exception as e:
            print(f"⚠️ 缓存读取失败（{e}），重来")
    return {"meta": {"prompt_version": PROMPT_VERSION}, "lines": {}}


def save_cache(cache):
    cache["meta"]["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    cache["meta"]["model"] = cache["meta"].get("model", DEFAULT_MODEL)
    cache["meta"]["count"] = len(cache["lines"])
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="给台词生成多样化的用户提问")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--ollama-url", default=DEFAULT_OLLAMA)
    ap.add_argument("--limit", type=int, help="只处理前 N 句（试跑用）")
    ap.add_argument("--batch", type=int, default=BATCH_SIZE)
    ap.add_argument("--dry-run", action="store_true", help="只统计，不生成")
    ap.add_argument("--retry-failed", action="store_true",
                    help="把上次没生成成功的重跑一遍（默认自动包含）")
    args = ap.parse_args()

    batch_size = args.batch

    lines = collect_lines()
    cache = load_cache()
    cache["meta"]["model"] = args.model
    todo = [ln for ln in lines if not cache["lines"].get(ln)]
    if args.limit:
        todo = todo[:args.limit]

    print("=" * 64)
    print("台词 → 用户提问生成")
    print("=" * 64)
    print(f"模型      : {args.model} @ {args.ollama_url}")
    print(f"需要提问的台词 : {len(lines)} 句")
    print(f"已有缓存  : {len(cache['lines'])} 句")
    print(f"本次待生成: {len(todo)} 句  （每批 {batch_size} 句，"
          f"约 {len(todo) / batch_size:.0f} 批）")

    if args.dry_run:
        print("\n（--dry-run：不实际生成）")
        return 0
    if not todo:
        print("\n✅ 没有待生成的，缓存已齐全")
        return 0

    print()
    t_start = time.time()
    failed_batches = []
    done = 0

    for bi in range(0, len(todo), batch_size):
        batch = todo[bi:bi + batch_size]
        bn = bi // batch_size + 1
        total_batches = (len(todo) + batch_size - 1) // batch_size
        try:
            raw = call_model(build_prompt(batch), args.model, args.ollama_url, batch_size)
            got = parse_questions(raw, len(batch))
        except Exception as e:
            failed_batches.append((bn, str(e)[:60]))
            print(f"  [{bn}/{total_batches}] ❌ {type(e).__name__}: {str(e)[:60]}")
            continue

        miss = 0
        for i, line in enumerate(batch):
            qs = clean_questions(got.get(i, []), line)
            if qs:
                cache["lines"][line] = qs
                done += 1
            else:
                miss += 1
        save_cache(cache)          # 每批存一次，中断不丢

        el = time.time() - t_start
        rate = done / el if el else 0
        eta = (len(todo) - done) / rate / 60 if rate else 0
        flag = f"  ⚠️ {miss} 句无有效问法" if miss else ""
        print(f"  [{bn}/{total_batches}] ✅ {len(batch)} 句"
              f"  累计 {done}/{len(todo)}  ({done/len(todo)*100:.0f}%)"
              f"  {rate:.2f} 句/s  剩余 {eta:.1f} 分钟{flag}")

    # 汇总
    el = time.time() - t_start
    print()
    print("=" * 64)
    print(f"完成：本次新增 {done} 句，缓存总计 {len(cache['lines'])} 句")
    print(f"耗时 {el/60:.1f} 分钟（{done/el:.2f} 句/s）")
    print(f"缓存文件：{CACHE_PATH}")
    if failed_batches:
        print(f"\n⚠️ {len(failed_batches)} 个批次失败（重跑本脚本会自动补）：")
        for bn, err in failed_batches[:8]:
            print(f"   批次 {bn}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
