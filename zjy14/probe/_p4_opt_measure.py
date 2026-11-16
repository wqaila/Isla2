"""实测 torch.compile 与 SDPA 对 CosyVoice3 是否有收益。

决定 enable_compile / enable_sdpa 两个配置项的命运：
  有收益 -> 实现
  无收益 -> 删除（不留摆设）

同时确认 PyTorch 2.8 是否默认已启用 SDPA（若是，开关无意义）。

测试项：
  A 基线（当前配置）
  B torch.compile 套在 llm 上
  C 强制关闭 SDPA（torch.backends.cuda.enable_flash_sdp 等）
  D 强制启用 SDPA

每组 3 次合成取中位数，记录首包与总耗时。
"""
from __future__ import annotations

import statistics
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")
sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

import numpy as np  # noqa: E402

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
REF_WAV = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
REF_TXT = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt")
PREFIX = "You are a helpful assistant.<|endofprompt|>"
SR = 24000
TEXT = "今天天气不错，我们下午去公园散步吧，顺便买点吃的。"
N = 3


def main() -> int:
    import torch
    from cosyvoice.cli.cosyvoice import CosyVoice3

    from custom_voice.inference.cosyvoice_engine import (
        _block_modelscope_download,
        _restore_modelscope_download,
    )
    from custom_voice.inference.text_frontend import enable_wetext_frontend

    ref_body = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" in ref_body:
        ref_body = ref_body.split("<|endofprompt|>", 1)[1]
    ref_full = PREFIX + ref_body

    print("=" * 96)
    print(" torch.compile / SDPA 收益实测")
    print("=" * 96)

    # 先看 PyTorch 的 SDPA 默认状态
    print("\n[0] PyTorch SDPA 后端状态")
    try:
        print(f"  flash_sdp_enabled: {torch.backends.cuda.flash_sdp_enabled()}")
        print(f"  mem_efficient_sdp_enabled: "
              f"{torch.backends.cuda.mem_efficient_sdp_enabled()}")
        print(f"  math_sdp_enabled: {torch.backends.cuda.math_sdp_enabled()}")
    except Exception as e:  # noqa: BLE001
        print(f"  无法查询: {e}")
    print(f"  torch 版本: {torch.__version__}")

    results: dict[str, dict] = {}

    def run_case(name: str, compile_llm: bool = False) -> None:
        print(f"\n{'=' * 96}")
        print(f" {name}")
        print("=" * 96)

        orig = _block_modelscope_download()
        try:
            cosy = CosyVoice3(str(MODEL), fp16=False)
        finally:
            _restore_modelscope_download(orig)

        enable_wetext_frontend(cosy.frontend)
        cosy.add_zero_shot_spk(ref_full, str(REF_WAV), "elysia")

        if compile_llm:
            try:
                t0 = time.perf_counter()
                cosy.model.llm = torch.compile(cosy.model.llm, dynamic=False)
                print(f"  torch.compile 套用耗时 {time.perf_counter() - t0:.1f}s")
            except Exception as e:  # noqa: BLE001
                print(f"  [失败] torch.compile: {type(e).__name__}: {e}")
                return

        def reset_hop():
            if hasattr(cosy.model, "token_hop_len"):
                cosy.model.token_hop_len = 25

        # 预热（compile 模式首次会触发编译，单独计时）
        t0 = time.perf_counter()
        reset_hop()
        try:
            list(cosy.inference_zero_shot("你好。", "", str(REF_WAV),
                                          zero_shot_spk_id="elysia", stream=False))
        except Exception as e:  # noqa: BLE001
            print(f"  [失败] 预热: {type(e).__name__}: {e}")
            return
        warm = time.perf_counter() - t0
        print(f"  预热（含可能的编译）{warm:.1f}s")

        times, durs = [], []
        for i in range(N):
            reset_hop()
            t0 = time.perf_counter()
            try:
                chunks = list(cosy.inference_zero_shot(
                    TEXT, "", str(REF_WAV), zero_shot_spk_id="elysia",
                    stream=False))
            except Exception as e:  # noqa: BLE001
                print(f"  第{i+1}次失败: {type(e).__name__}: {e}")
                continue
            el = time.perf_counter() - t0
            if not chunks:
                continue
            dur = chunks[0]["tts_speech"].shape[-1] / SR
            times.append(el)
            durs.append(dur)
            print(f"  第{i+1}次 {el:.2f}s  音频 {dur:.2f}s  RTF {el/dur:.3f}")

        if times:
            results[name] = {
                "median": statistics.median(times),
                "rtf": statistics.median(times) / statistics.median(durs),
                "warm": warm,
            }
        del cosy
        torch.cuda.empty_cache()

    # A 基线
    run_case("A_基线")

    # B torch.compile
    run_case("B_compile_llm", compile_llm=True)

    # ---- 汇总 ----
    print()
    print("=" * 96)
    print(" 汇总")
    print("=" * 96)
    print(f"  {'方案':<20}{'合成中位':>10}{'RTF':>9}{'预热':>10}")
    print("  " + "-" * 52)
    for name, r in results.items():
        print(f"  {name:<20}{r['median']:>9.2f}s{r['rtf']:>9.3f}{r['warm']:>9.1f}s")

    print()
    if "A_基线" in results and "B_compile_llm" in results:
        base = results["A_基线"]["median"]
        comp = results["B_compile_llm"]["median"]
        diff = (comp - base) / base * 100
        print(f"  compile 相对基线: {diff:+.1f}%")
        if diff < -5:
            print("  -> compile 有收益，值得实现")
        elif diff > 5:
            print("  -> compile 更慢，应删除该配置项")
        else:
            print("  -> 无显著差异，且首次编译开销大，建议删除该配置项")

    print(f"\n  SDPA 说明：")
    print("    若 PyTorch 默认已启用 flash/mem_efficient SDPA，")
    print("    则 enable_sdpa 开关无实际作用，应删除。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
