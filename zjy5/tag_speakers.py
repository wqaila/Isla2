#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给现有语料打「角色相似度」分 —— 找出混在台词里的旁白与其他角色。

【为什么需要】
`zjy7/downloads/*.txt` 是 17 个文件约 4000 句，里面混着：
  - 爱莉希雅本人的台词
  - **其他角色的台词**（`2.txt` 里有芽衣/克莱因/伊甸/梅比乌斯/格蕾修）
  - **旁白与游戏 UI**（「（选项：…」「系统提示：…」）
  - **OCR 连行拼接产物**（整屏文字连成一坨，最长 7541 字）
这就是**人设污染** —— 比数据少更糟：模型会学到「爱莉希雅会说这种话」。

【为什么要单独写一个脚本，不用 main.py 的 --speaker-filter】
`main.py` 的打标是**采集流水线内部**的一步，需要重跑 OCR。
本脚本直接对**已经落盘的 txt** 补打标 —— 源视频只剩 1 个，
重跑 OCR 已不现实，这是唯一能补救的路径。

【用法】

    python tag_speakers.py --dry-run          # 只看要处理多少句
    python tag_speakers.py --limit 200        # 先小批量验证
    python tag_speakers.py                    # 全量（约 4000 句，10~15 分钟）
    python tag_speakers.py --filtered-dir out # 额外输出过滤后的 txt

输出：`speaker_tags/<原名>.jsonl`，每行 `{"text","speaker_score","file"}`。
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "chibtaici"))
from speaker_filter import LlmSpeakerFilter  # noqa: E402

PROJECT = Path(__file__).resolve().parent
SRC_DIR = PROJECT.parent / "zjy7" / "downloads"
OUT_DIR = PROJECT / "speaker_tags"

# 与 prepare_data 的长度红线一致：太长的一句先切开再打分，
# 否则「整屏连行 blob」会被当成一句话打一个分（毫无意义）。
SPLIT_LIMIT = 100
MIN_PIECE = 4
_SENT_END = "。！？!?…~♪；;"


def split_pieces(text, limit=SPLIT_LIMIT, min_piece=MIN_PIECE):
    """按句末标点切分超长文本（与 zjy7 的 split_long_text 同思路）。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text] if len(text) >= min_piece else []
    pieces, buf = [], ""
    for ch in text:
        buf += ch
        if ch in _SENT_END and len(buf) >= min_piece:
            pieces.append(buf.strip())
            buf = ""
        elif len(buf) >= limit:
            pieces.append(buf.strip())
            buf = ""
    if buf.strip() and len(buf.strip()) >= min_piece:
        pieces.append(buf.strip())
    return pieces


def collect():
    """收集 (文件, 句子) 列表。"""
    items = []
    for fp in sorted(SRC_DIR.glob("*.txt"),
                     key=lambda p: int(p.stem) if p.stem.isdigit() else 0):
        raw = fp.read_text(encoding="utf-8", errors="replace")
        for line in raw.splitlines():
            for piece in split_pieces(line):
                items.append((fp.name, piece))
    return items


def main():
    ap = argparse.ArgumentParser(description="给现有语料打角色相似度分")
    ap.add_argument("--limit", type=int, help="只处理前 N 句（试跑）")
    ap.add_argument("--threshold", type=int, default=3,
                    help="低于这个分算「不是角色本人」（默认 3）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--filtered-dir", help="额外输出过滤后的 txt 到这个目录")
    args = ap.parse_args()

    items = collect()
    if args.limit:
        items = items[:args.limit]

    print("=" * 64)
    print("语料角色相似度打标")
    print("=" * 64)
    print(f"来源    : {SRC_DIR}")
    print(f"待打标  : {len(items)} 句")

    if args.dry_run:
        by_file = Counter(f for f, _ in items)
        for name, n in sorted(by_file.items()):
            print(f"    {name:>8}  {n:5} 句")
        print("\n（--dry-run：不实际打标）")
        return 0

    f = LlmSpeakerFilter(threshold=args.threshold)
    print(f"模型    : {f.model}")
    if not f.check_available():
        print(f"\n❌ Ollama 不可用或模型 `{f.model}` 不存在。")
        print("   注意：本模块失败时是「静默跳过打分」，所以这里显式拦住。")
        return 2

    OUT_DIR.mkdir(exist_ok=True)
    t0 = time.time()
    texts = [t for _, t in items]
    scores, stats = f.score_texts(texts)
    el = time.time() - t0

    # 按文件写 jsonl
    by_file = {}
    for (name, text), sc in zip(items, scores):
        by_file.setdefault(name, []).append({"text": text, "speaker_score": sc,
                                             "file": name})
    for name, rows in by_file.items():
        (OUT_DIR / f"{Path(name).stem}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
            encoding="utf-8")

    # 汇总
    dist = Counter(s for s in scores)
    ok = sum(1 for s in scores if s is not None and s >= args.threshold)
    bad = sum(1 for s in scores if s is not None and s < args.threshold)
    unscored = sum(1 for s in scores if s is None)

    print(f"\n耗时 {el/60:.1f} 分钟   {stats}")
    print(f"\n=== 分数分布 ===")
    for s in sorted(dist, key=lambda x: (x is None, x)):
        label = "未打分" if s is None else f"{s} 分"
        bar = "█" * int(dist[s] / max(dist.values()) * 30)
        print(f"  {label:>6}  {dist[s]:5}  {bar}")
    print(f"\n判定（阈值 {args.threshold}）：")
    print(f"  ✅ 像角色本人  {ok:5} 句 ({ok/max(len(scores),1)*100:.1f}%)")
    print(f"  ❌ 不像（该剔） {bad:5} 句 ({bad/max(len(scores),1)*100:.1f}%)")
    if unscored:
        print(f"  ⚠️ 未打分      {unscored:5} 句（保留）")
    print(f"\n输出: {OUT_DIR}")

    if args.filtered_dir:
        out = Path(args.filtered_dir)
        out.mkdir(parents=True, exist_ok=True)
        kept = 0
        for name, rows in by_file.items():
            good = [r["text"] for r in rows
                    if r["speaker_score"] is None or r["speaker_score"] >= args.threshold]
            kept += len(good)
            (out / name).write_text("\n".join(good), encoding="utf-8")
        print(f"过滤后语料: {out}（{kept} 句）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
