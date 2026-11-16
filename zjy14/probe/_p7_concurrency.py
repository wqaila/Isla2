"""Phase 7：并发显存实测。

要回答：max_concurrent_requests 能否从 1 提到 2？
  判据：2 并发时显存峰值是否超出安全余量（默认要求留 1GB）

设计：
  1. 先测单并发显存峰值
  2. 再测 2 并发显存峰值
  3. 对比增长，判断是否可放开

注意：必须用真并发（两个线程同时调引擎），
而不是顺序调用 —— 顺序调用不会叠加中间张量。
"""
from __future__ import annotations

import json
import statistics
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")
sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

OUT = Path("/mnt/d/xiangmudaima/zjy14/_p7_concurrency")
OUT.mkdir(parents=True, exist_ok=True)

TEXTS = [
    "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。",
    "我最近在调试一块开发板，屏幕驱动那边一直有点问题，你有空能帮我看看吗？",
]


def vram() -> dict:
    import torch

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


def main() -> int:
    import torch

    from custom_voice.config.loader import load_config
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine
    from custom_voice.schemas.synthesis import SynthesisRequest

    print("=" * 90)
    print(" 并发显存实测：单并发 vs 2 并发")
    print("=" * 90)

    cfg = load_config(
        overrides={
            "engine": "cosyvoice3",
            "device": "cuda",
            "text_frontend": "auto",
            "memory_safety_margin_mb": 0,
        }
    )

    engine = CosyVoiceEngine(cfg)
    engine.load()
    print(f"  引擎就绪（加载 {engine.stats().load_s:.1f}s）")

    base = vram()
    print(f"  基线: allocated={base.get('allocated_mb')}MB "
          f"reserved={base.get('reserved_mb')}MB free={base.get('free_mb')}MB")

    results = {}

    # ---- 单并发 ----
    print("\n[1] 单并发（顺序 3 次）")
    torch.cuda.reset_peak_memory_stats()
    times_single = []
    for i in range(3):
        t0 = time.perf_counter()
        r = engine.synthesize(SynthesisRequest(text=TEXTS[i % 2], voice_id="elysia"))
        el = time.perf_counter() - t0
        times_single.append(el)
        print(f"    第{i+1}次 {el:.2f}s  音频 {r.duration_s:.2f}s  RTF {r.rtf:.3f}")

    v1 = vram()
    print(f"  单并发峰值: allocated={v1['peak_allocated_mb']}MB "
          f"reserved={v1['peak_reserved_mb']}MB")
    results["single"] = {
        "peak_allocated_mb": v1["peak_allocated_mb"],
        "peak_reserved_mb": v1["peak_reserved_mb"],
        "median_synth_s": round(statistics.median(times_single), 3),
        "free_mb_after": v1["free_mb"],
    }

    # ---- 2 并发 ----
    print("\n[2] 2 并发（两个线程同时合成）")
    torch.cuda.reset_peak_memory_stats()

    barrier = threading.Barrier(2)
    errors: list[str] = []

    def worker(idx: int) -> float:
        try:
            barrier.wait(timeout=10)  # 确保真正同时开始
            t0 = time.perf_counter()
            engine.synthesize(
                SynthesisRequest(text=TEXTS[idx % 2], voice_id="elysia")
            )
            return time.perf_counter() - t0
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")
            return -1.0

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        times_conc = list(ex.map(worker, range(2)))
    wall = time.perf_counter() - t0

    print(f"    墙钟 {wall:.2f}s  各请求 {[round(t,2) for t in times_conc]}")
    if errors:
        print(f"    [错误] {errors}")

    v2 = vram()
    print(f"  2 并发峰值: allocated={v2['peak_allocated_mb']}MB "
          f"reserved={v2['peak_reserved_mb']}MB")
    results["concurrent2"] = {
        "peak_allocated_mb": v2["peak_allocated_mb"],
        "peak_reserved_mb": v2["peak_reserved_mb"],
        "wall_s": round(wall, 3),
        "times": [round(t, 3) for t in times_conc],
        "free_mb_after": v2["free_mb"],
        "errors": errors,
    }

    # ---- 分析 ----
    print()
    print("=" * 90)
    print(" 分析")
    print("=" * 90)

    delta_alloc = v2["peak_allocated_mb"] - v1["peak_allocated_mb"]
    delta_resv = v2["peak_reserved_mb"] - v1["peak_reserved_mb"]

    print(f"  单并发峰值  allocated {v1['peak_allocated_mb']:.0f}MB  "
          f"reserved {v1['peak_reserved_mb']:.0f}MB")
    print(f"  2并发峰值   allocated {v2['peak_allocated_mb']:.0f}MB  "
          f"reserved {v2['peak_reserved_mb']:.0f}MB")
    print(f"  增量        allocated {delta_alloc:+.0f}MB  "
          f"reserved {delta_resv:+.0f}MB")

    total = base.get("total_mb", 12227)
    margin_needed = 1024
    free_after = v2["free_mb"]
    print(f"\n  2 并发后剩余显存 {free_after:.0f}MB（安全余量要求 {margin_needed}MB）")

    # 串行 vs 并行的墙钟对比
    if not errors and all(t > 0 for t in times_conc):
        serial_est = sum(times_conc)
        speedup = serial_est / wall if wall > 0 else 0
        print(f"\n  串行估算 {serial_est:.2f}s  实际墙钟 {wall:.2f}s  "
              f"加速比 {speedup:.2f}x")
        if speedup > 1.3:
            print("  [结论] 2 并发确实并行执行")
        else:
            print("  [结论] 2 并发几乎无加速（可能受调度器或显存限制）")

    print()
    can_raise = (
        not errors
        and free_after >= margin_needed
        and delta_alloc < 1024
    )
    if can_raise:
        print("  [PASS] 2 并发可行：无错误、显存充足、增量合理")
        print(f"         建议可把 max_concurrent_requests 设为 2")
        print(f"         但需注意：当前调度器默认 1，改配置后需重启验证")
    else:
        reasons = []
        if errors:
            reasons.append(f"有错误 {errors[:1]}")
        if free_after < margin_needed:
            reasons.append(f"剩余显存 {free_after:.0f}MB < {margin_needed}MB")
        if delta_alloc >= 1024:
            reasons.append(f"显存增量 {delta_alloc:.0f}MB 过大")
        print(f"  [FAIL] 不建议放开到 2 并发：{'；'.join(reasons)}")

    report = {
        "baseline": base,
        "single": results["single"],
        "concurrent2": results["concurrent2"],
        "delta_allocated_mb": round(delta_alloc, 1),
        "delta_reserved_mb": round(delta_resv, 1),
        "free_after_2x_mb": free_after,
        "margin_required_mb": margin_needed,
        "can_raise_to_2": can_raise,
    }
    (OUT / "concurrency.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    engine.shutdown()
    print(f"\n  报告: {OUT / 'concurrency.json'}")
    return 0 if can_raise else 1


if __name__ == "__main__":
    raise SystemExit(main())
