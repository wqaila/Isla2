"""验证 enable_compile 配置项真正生效。

要求：
  1. 配置 enable_compile=true 时，diagnostics 里 compile_applied 应为 True
  2. 配置 false 时，compile_applied 为 False
  3. 两种情况下合成都能正常出音频
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")
sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
REF_WAV = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
REF_TXT = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt")
TEXT = "今天天气不错，我们下午去公园散步吧，顺便买点吃的。"


def main() -> int:
    from custom_voice.config.loader import load_config
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine
    from custom_voice.schemas.synthesis import SynthesisRequest

    print("=" * 88)
    print(" enable_compile 配置生效验证")
    print("=" * 88)

    results = {}

    for label, enabled in (("关闭", False), ("开启", True)):
        print(f"\n{'=' * 88}")
        print(f" enable_compile = {enabled}（{label}）")
        print("=" * 88)

        cfg = load_config(
            overrides={
                "engine": "cosyvoice3",
                "device": "cuda",
                "enable_compile": enabled,
                "text_frontend": "auto",
                "memory_safety_margin_mb": 0,
            }
        )
        print(f"  配置 enable_compile = {cfg.enable_compile}")

        e = CosyVoiceEngine(cfg)
        t0 = time.perf_counter()
        try:
            e.load()
        except Exception as exc:  # noqa: BLE001
            print(f"  [失败] load: {type(exc).__name__}: {exc}")
            continue
        load_s = time.perf_counter() - t0
        print(f"  加载 + 预热 {load_s:.1f}s")

        diag = e.diagnostics()
        print(f"  diagnostics.compile_requested = {diag['compile_requested']}")
        print(f"  diagnostics.compile_applied   = {diag['compile_applied']}")

        # 合成一次确认可用
        t0 = time.perf_counter()
        try:
            r = e.synthesize(SynthesisRequest(text=TEXT, voice_id="elysia"))
        except Exception as exc:  # noqa: BLE001
            print(f"  [失败] 合成: {type(exc).__name__}: {exc}")
            e.shutdown()
            continue
        synth_s = time.perf_counter() - t0
        print(f"  合成 {synth_s:.2f}s  音频 {r.duration_s:.2f}s  RTF {r.rtf:.3f}")

        results[label] = {
            "requested": diag["compile_requested"],
            "applied": diag["compile_applied"],
            "load_s": load_s,
            "synth_s": synth_s,
            "ok": r.duration_s > 0,
        }

        e.shutdown()
        try:
            import torch

            torch.cuda.empty_cache()
        except ImportError:
            pass

    # ---- 判定 ----
    print()
    print("=" * 88)
    print(" 判定")
    print("=" * 88)
    ok = True

    off = results.get("关闭")
    on = results.get("开启")

    if off:
        if off["requested"] is False and off["applied"] is False:
            print("  [PASS] 关闭时：requested=False, applied=False")
        else:
            print(f"  [FAIL] 关闭时状态异常: {off}")
            ok = False
        if off["ok"]:
            print("  [PASS] 关闭时合成正常")
        else:
            print("  [FAIL] 关闭时合成失败")
            ok = False

    if on:
        if on["requested"] is True and on["applied"] is True:
            print("  [PASS] 开启时：requested=True, applied=True（compile 真正生效）")
        else:
            print(f"  [FAIL] 开启时 compile 未生效: {on}")
            ok = False
        if on["ok"]:
            print("  [PASS] 开启时合成正常")
        else:
            print("  [FAIL] 开启时合成失败")
            ok = False

    if off and on:
        print(f"\n  加载耗时：关闭 {off['load_s']:.1f}s / 开启 {on['load_s']:.1f}s")
        print(f"  首次合成：关闭 {off['synth_s']:.2f}s / 开启 {on['synth_s']:.2f}s")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
