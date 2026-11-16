"""稳定性压测：连续合成 + 显存/延迟趋势监测。

Phase 7 要回答的问题：
  Q1 连续合成 N 次后，显存是否持续增长（泄漏）
  Q2 RTF 是否随时间劣化（缓存污染、碎片化）
  Q3 首包延迟是否漂移（token_hop_len 之类的跨调用污染）
  Q4 并发 2 请求时显存峰值多少，是否会 OOM

设计：
  - 直接调引擎（不经 HTTP），排除网络干扰
  - 每轮记录：显存（allocated/reserved/free）、耗时、RTF、音频时长
  - 每 10 轮打印一次进度，避免刷屏
  - 结束时做趋势分析：线性回归斜率 + 前后半段对比

用法：
  wsl -e bash probe/run_d1.sh probe/_p7_stability.py
  wsl -e bash probe/run_d1.sh probe/_p7_stability.py --minutes 30
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")
sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_p7_stability")
OUT.mkdir(parents=True, exist_ok=True)

# 轮换文本，避免单一文本被缓存"优化"而掩盖问题
TEXTS = [
    "今天天气真不错，我们一起出去走走吧。",
    "昨天下了整整一天的雨，窗外的树叶被打得七零八落。",
    "我最近在调试一块开发板，屏幕驱动那边一直有点问题。",
    "晚上的风有点凉，记得把窗户关好，别着凉了。",
    "你要是累了就先休息，剩下的事情明天再说也不迟。",
]


def vram_snapshot() -> dict:
    """采集显存快照。"""
    try:
        import torch
    except ImportError:
        return {}

    if not torch.cuda.is_available():
        return {}

    free, total = torch.cuda.mem_get_info()
    return {
        "allocated_mb": round(torch.cuda.memory_allocated(0) / 1024**2, 1),
        "reserved_mb": round(torch.cuda.memory_reserved(0) / 1024**2, 1),
        "peak_allocated_mb": round(torch.cuda.max_memory_allocated(0) / 1024**2, 1),
        "peak_reserved_mb": round(torch.cuda.max_memory_reserved(0) / 1024**2, 1),
        "free_mb": round(free / 1024**2, 1),
        "total_mb": round(total / 1024**2, 1),
    }


def slope(values: list[float]) -> float:
    """线性回归斜率（单位/轮）。用于判断趋势。"""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(values) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, values))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=5.0,
                    help="压测时长（分钟），默认 5，Phase 7 正式测试用 30")
    ap.add_argument("--max-rounds", type=int, default=0,
                    help="最大轮数，0 表示不限制（按时间停）")
    args = ap.parse_args()

    from custom_voice.config.loader import load_config
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine
    from custom_voice.schemas.synthesis import SynthesisRequest

    duration_s = args.minutes * 60

    print("=" * 96)
    print(f" 稳定性压测：{args.minutes:.0f} 分钟连续合成")
    print("=" * 96)

    # ---- 冷启动测量 ----
    cfg = load_config(
        overrides={
            "engine": "cosyvoice3",
            "device": "cuda",
            "text_frontend": "auto",
            "memory_safety_margin_mb": 0,
        }
    )

    print("\n[冷启动] 加载模型...")
    t0 = time.perf_counter()
    engine = CosyVoiceEngine(cfg)
    engine.load()
    cold_load_s = time.perf_counter() - t0
    print(f"  冷启动耗时 {cold_load_s:.2f}s")

    # 冷启动首次合成
    t0 = time.perf_counter()
    r = engine.synthesize(SynthesisRequest(text=TEXTS[0], voice_id="elysia"))
    cold_first_synth_s = time.perf_counter() - t0
    print(f"  冷启动首次合成 {cold_first_synth_s:.2f}s  RTF {r.rtf:.3f}")

    vram_baseline = vram_snapshot()
    print(f"  基线显存 allocated={vram_baseline.get('allocated_mb')}MB "
          f"reserved={vram_baseline.get('reserved_mb')}MB "
          f"free={vram_baseline.get('free_mb')}MB")

    # ---- 连续压测 ----
    print(f"\n[连续压测] 目标 {args.minutes:.0f} 分钟...")
    print(f"  {'轮':>4}{'耗时s':>8}{'音频s':>8}{'RTF':>7}"
          f"{'allocMB':>9}{'resvMB':>9}{'freeMB':>9}{'峰值MB':>9}")
    print("  " + "-" * 66)

    records: list[dict] = []
    started = time.perf_counter()
    round_no = 0

    try:
        while True:
            elapsed = time.perf_counter() - started
            if elapsed >= duration_s:
                break
            if args.max_rounds and round_no >= args.max_rounds:
                break

            text = TEXTS[round_no % len(TEXTS)]
            t0 = time.perf_counter()
            try:
                res = engine.synthesize(
                    SynthesisRequest(text=text, voice_id="elysia")
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  {round_no + 1:>4}  [失败] {type(exc).__name__}: {exc}")
                records.append({
                    "round": round_no + 1,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                round_no += 1
                continue

            el = time.perf_counter() - t0
            v = vram_snapshot()
            rec = {
                "round": round_no + 1,
                "elapsed_s": round(elapsed, 1),
                "synth_s": round(el, 3),
                "audio_s": round(res.duration_s, 3),
                "rtf": round(res.rtf, 4),
                **v,
            }
            records.append(rec)

            # 每 10 轮或首轮打印
            if round_no % 10 == 0 or round_no == 0:
                print(f"  {rec['round']:>4}{rec['synth_s']:>7.2f}"
                      f"{rec['audio_s']:>8.2f}{rec['rtf']:>7.3f}"
                      f"{rec.get('allocated_mb', 0):>9.0f}"
                      f"{rec.get('reserved_mb', 0):>9.0f}"
                      f"{rec.get('free_mb', 0):>9.0f}"
                      f"{rec.get('peak_allocated_mb', 0):>9.0f}")

            round_no += 1
    except KeyboardInterrupt:
        print("\n  用户中断")

    total_s = time.perf_counter() - started

    # ---- 分析 ----
    ok = [r for r in records if "error" not in r]
    errors = [r for r in records if "error" in r]

    print()
    print("=" * 96)
    print(" 分析")
    print("=" * 96)

    if not ok:
        print("  [FAIL] 没有任何成功的合成")
        engine.shutdown()
        return 1

    print(f"  总轮数 {len(records)}（成功 {len(ok)}，失败 {len(errors)}）")
    print(f"  总时长 {total_s / 60:.1f} 分钟")
    print(f"  平均每轮 {total_s / len(records):.2f}s")

    # RTF 趋势
    rtfs = [r["rtf"] for r in ok]
    synth_times = [r["synth_s"] for r in ok]
    print(f"\n  RTF: 中位 {statistics.median(rtfs):.3f}  "
          f"均值 {statistics.mean(rtfs):.3f}  "
          f"范围 {min(rtfs):.3f}–{max(rtfs):.3f}")
    print(f"  耗时: 中位 {statistics.median(synth_times):.2f}s  "
          f"均值 {statistics.mean(synth_times):.2f}s")

    # 前后半段对比（判断劣化）
    half = len(ok) // 2
    if half >= 3:
        first_half = synth_times[:half]
        second_half = synth_times[half:]
        d = statistics.median(second_half) - statistics.median(first_half)
        pct = d / statistics.median(first_half) * 100
        print(f"\n  前后半段耗时对比:")
        print(f"    前半 {statistics.median(first_half):.2f}s  "
              f"后半 {statistics.median(second_half):.2f}s  "
              f"变化 {pct:+.1f}%")
        if abs(pct) < 10:
            print("    [PASS] 无显著劣化")
        elif pct > 0:
            print(f"    [WARN] 后半段变慢 {pct:.1f}%，需关注")
        else:
            print(f"    [OK] 后半段更快 {abs(pct):.1f}%（缓存生效）")

    # 显存趋势（关键：泄漏检测）
    allocs = [r.get("allocated_mb", 0) for r in ok]
    reserved = [r.get("reserved_mb", 0) for r in ok]

    if allocs and any(allocs):
        s_alloc = slope(allocs)
        s_resv = slope(reserved)
        print(f"\n  显存趋势（线性回归斜率，MB/轮）:")
        print(f"    allocated: {s_alloc:+.3f}  "
              f"首轮 {allocs[0]:.0f}MB -> 末轮 {allocs[-1]:.0f}MB")
        print(f"    reserved:  {s_resv:+.3f}  "
              f"首轮 {reserved[0]:.0f}MB -> 末轮 {reserved[-1]:.0f}MB")

        growth = allocs[-1] - allocs[0]
        print(f"    allocated 净增长 {growth:+.1f}MB")

        # 判据：净增长 < 50MB 且斜率 < 0.5 MB/轮 视为无泄漏
        if growth < 50 and abs(s_alloc) < 0.5:
            print("    [PASS] 无显存泄漏迹象")
        elif growth < 200:
            print(f"    [WARN] 有轻微增长 {growth:.0f}MB，需观察更长周期")
        else:
            print(f"    [FAIL] 显存持续增长 {growth:.0f}MB，疑似泄漏")

        peak = max(r.get("peak_allocated_mb", 0) for r in ok)
        print(f"    峰值 allocated {peak:.0f}MB")
    else:
        print("\n  [SKIP] 无显存数据（可能未用 CUDA）")

    # ---- 热重启对比 ----
    print(f"\n  冷启动 vs 热启动:")
    print(f"    冷启动加载 {cold_load_s:.2f}s")
    print(f"    冷启动首次合成 {cold_first_synth_s:.2f}s")
    print(f"    热态首次合成 {ok[0]['synth_s']:.2f}s（压测首轮）")
    print(f"    热态稳态 {statistics.median(synth_times):.2f}s")

    # ---- 保存 ----
    report = {
        "minutes": args.minutes,
        "total_rounds": len(records),
        "ok_rounds": len(ok),
        "error_rounds": len(errors),
        "total_s": round(total_s, 1),
        "cold_load_s": round(cold_load_s, 2),
        "cold_first_synth_s": round(cold_first_synth_s, 2),
        "rtf_median": round(statistics.median(rtfs), 4),
        "rtf_mean": round(statistics.mean(rtfs), 4),
        "synth_median_s": round(statistics.median(synth_times), 3),
        "vram_baseline": vram_baseline,
        "vram_final": vram_snapshot(),
        "vram_alloc_slope": round(slope(allocs), 3) if allocs else 0,
        "vram_growth_mb": round(allocs[-1] - allocs[0], 1) if allocs else 0,
        "errors": errors[:10],
        "records": records,
    }
    (OUT / "stability.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    engine.shutdown()
    print(f"\n  报告: {OUT / 'stability.json'}")

    # 判定
    failed = bool(errors) or (allocs and (allocs[-1] - allocs[0]) >= 200)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
