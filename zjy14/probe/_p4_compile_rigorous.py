"""严谨复测 torch.compile：交错采样排除顺序效应。

上一轮结果可疑：
  A 基线    3.59 / 2.94 / 3.35  中位 3.35
  B compile 3.15 / 2.94 / 3.39  中位 3.15
  第 2、3 次几乎相同（2.94 vs 2.94，3.35 vs 3.39），
  只有第 1 次不同 —— 那是预热残留，不是真实差异。

正确做法：
  1. 同一个进程里同时持有两个模型实例（基线与 compile）
  2. 交错执行 A B A B A B ...，消除时间漂移
  3. 用 8 轮，取中位数与配对差值
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
ROUNDS = 8


def build(compile_llm: bool):  # noqa: ANN201
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

    orig = _block_modelscope_download()
    try:
        cosy = CosyVoice3(str(MODEL), fp16=False)
    finally:
        _restore_modelscope_download(orig)

    enable_wetext_frontend(cosy.frontend)
    cosy.add_zero_shot_spk(ref_full, str(REF_WAV), "elysia")

    if compile_llm:
        cosy.model.llm = torch.compile(cosy.model.llm, dynamic=False)

    return cosy


def synth_once(cosy) -> float:  # noqa: ANN001
    if hasattr(cosy.model, "token_hop_len"):
        cosy.model.token_hop_len = 25
    t0 = time.perf_counter()
    chunks = list(cosy.inference_zero_shot(
        TEXT, "", str(REF_WAV), zero_shot_spk_id="elysia", stream=False))
    el = time.perf_counter() - t0
    return el if chunks else -1.0


def main() -> int:
    import torch

    print("=" * 90)
    print(" torch.compile 严谨复测（交错采样，排除顺序效应）")
    print("=" * 90)
    print(f"  {ROUNDS} 轮，每轮 A(基线) -> B(compile) 交错")

    print("\n构建两个模型实例...")
    a = build(False)
    print("  A 基线就绪")
    b = build(True)
    print("  B compile 就绪")

    # 各自预热两次（compile 首次会触发编译）
    for _ in range(2):
        synth_once(a)
        synth_once(b)
    print("  预热完成")

    a_times: list[float] = []
    b_times: list[float] = []

    print(f"\n  {'轮次':<6}{'A基线':>10}{'B_compile':>12}{'差值':>10}")
    print("  " + "-" * 40)
    for i in range(ROUNDS):
        ta = synth_once(a)
        tb = synth_once(b)
        if ta > 0 and tb > 0:
            a_times.append(ta)
            b_times.append(tb)
            print(f"  {i+1:<6}{ta:>9.3f}s{tb:>11.3f}s{tb - ta:>+9.3f}s")

    print()
    print("=" * 90)
    print(" 统计")
    print("=" * 90)
    ma = statistics.median(a_times)
    mb = statistics.median(b_times)
    print(f"  A 基线    中位 {ma:.3f}s  均值 {statistics.mean(a_times):.3f}s  "
          f"标准差 {statistics.stdev(a_times):.3f}s")
    print(f"  B compile 中位 {mb:.3f}s  均值 {statistics.mean(b_times):.3f}s  "
          f"标准差 {statistics.stdev(b_times):.3f}s")

    diffs = [tb - ta for ta, tb in zip(a_times, b_times)]
    md = statistics.median(diffs)
    print(f"\n  配对差值中位 {md:+.3f}s  均值 {statistics.mean(diffs):+.3f}s")
    print(f"  相对改善 {(ma - mb) / ma * 100:+.1f}%")

    # 显著性粗判：差值均值是否超过基线标准差的一半
    noise = statistics.stdev(a_times) / 2
    print(f"\n  噪声参考（基线标准差/2）= {noise:.3f}s")
    print(f"  |配对差值均值| = {abs(statistics.mean(diffs)):.3f}s")

    print()
    print("=" * 90)
    print(" 结论")
    print("=" * 90)
    if abs(statistics.mean(diffs)) < noise:
        print("  [无显著差异] compile 的收益在噪声范围内")
        print("  -> 建议删除 enable_compile 配置项（首次编译开销大，收益不明确）")
    elif statistics.mean(diffs) < 0:
        print(f"  [有收益] compile 快 {abs(statistics.mean(diffs)):.3f}s")
        print("  -> 可考虑实现，但需权衡首次编译开销")
    else:
        print("  [更慢] compile 反而慢")
        print("  -> 应删除 enable_compile 配置项")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
