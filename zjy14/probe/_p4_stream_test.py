"""Phase 4 前置验证：在 zjy14 当前环境重测流式效果。

为什么要重测：
  zjy13 的流式数据是 2026-10-26 测的，当时参考音频是官方示例、
  文本前端未启用、token_hop_len 有污染。
  现在这些前提都变了（自建参考、wetext 启用、hop_len 修复），
  必须重新测才能定长短句路由阈值。

同时验证：
  Q1 流式 vs 非流式的首包延迟对比（短/中/长三种长度）
  Q2 流式分块的实际块数与块大小
  Q3 分块拼接处是否有咔哒声（能量突变检测）
  Q4 长短句路由的合适阈值
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
import soundfile as sf  # noqa: E402

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
REF_WAV = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
REF_TXT = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_p4_stream")
OUT.mkdir(parents=True, exist_ok=True)

SR = 24000
PREFIX = "You are a helpful assistant.<|endofprompt|>"
N_REPEAT = 3

# 三档长度，覆盖路由阈值判定
TEXTS = {
    "短句_6字": "你好呀，今天过得怎么样？",
    "中句_20字": "今天天气不错，我们下午去公园散步吧，顺便买点吃的。",
    "长句_45字": (
        "我最近在调试一块 ESP32-S3 的开发板，屏幕驱动那边一直有点问题，"
        "你有空的话能不能帮我看看？"
    ),
}


def count_chars(t: str) -> int:
    return len([c for c in t if c not in "，。！？、；：,.!?;: \n\t"])


def to_pcm16(speech) -> bytes:  # noqa: ANN001
    arr = speech.detach().to("cpu", dtype=np.float32_type if False else None).numpy() \
        if False else speech.detach().cpu().float().numpy()
    if arr.ndim > 1:
        arr = arr.squeeze()
    arr = np.clip(arr, -1.0, 1.0)
    return np.round(arr * 32767.0).astype("<i2").tobytes()


def detect_clicks(pcm: bytes, sr: int) -> dict:
    """检测拼接处爆音：相邻样本的突变。

    爆音的特征是单个样本间出现远大于正常范围的跳变。
    """
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
    if len(x) < 3:
        return {"max_jump": 0.0, "clicks": 0}
    d = np.abs(np.diff(x))
    # 正常语音相邻样本差值通常 < 0.3；爆音会接近 1.0
    threshold = 0.6
    clicks = int(np.sum(d > threshold))
    return {"max_jump": float(d.max()), "clicks": clicks}


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3
    from custom_voice.inference.text_frontend import enable_wetext_frontend

    ref_body = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" in ref_body:
        ref_body = ref_body.split("<|endofprompt|>", 1)[1]
    ref_full = PREFIX + ref_body

    print("=" * 104)
    print(" Phase 4 前置验证：流式效果重测（zjy14 当前环境）")
    print("=" * 104)
    print(f"  参考正文 {count_chars(ref_body)} 字符 / 每种文本 {N_REPEAT} 次")

    # 阻止启动期联网
    from custom_voice.inference.cosyvoice_engine import (
        _block_modelscope_download,
        _restore_modelscope_download,
    )

    orig = _block_modelscope_download()
    try:
        cosy = CosyVoice3(str(MODEL), fp16=False)
    finally:
        _restore_modelscope_download(orig)

    enable_wetext_frontend(cosy.frontend)
    print(f"  text_frontend = {cosy.frontend.text_frontend!r}")
    cosy.add_zero_shot_spk(ref_full, str(REF_WAV), "elysia")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    reset_hop()
    list(cosy.inference_zero_shot("你好。", "", str(REF_WAV),
                                  zero_shot_spk_id="elysia", stream=False))
    print("  预热完成")

    rows = []

    for label, text in TEXTS.items():
        nc = count_chars(text)
        print()
        print("-" * 104)
        print(f" {label}（{nc} 字）")
        print("-" * 104)

        # ---- 非流式 ----
        non_first, non_total, non_dur = [], [], []
        for i in range(N_REPEAT):
            reset_hop()
            t0 = time.perf_counter()
            chunks = list(cosy.inference_zero_shot(
                text, "", str(REF_WAV), zero_shot_spk_id="elysia", stream=False))
            total = time.perf_counter() - t0
            if not chunks:
                continue
            pcm = to_pcm16(chunks[0]["tts_speech"])
            dur = len(pcm) / 2 / SR
            # 非流式首包 = 全部完成时间
            non_first.append(total)
            non_total.append(total)
            non_dur.append(dur)
            sf.write(str(OUT / f"{label}_non_{i+1}.wav"),
                     np.frombuffer(pcm, dtype="<i2"), SR, subtype="PCM_16")

        # ---- 流式 ----
        st_first, st_total, st_dur, st_n = [], [], [], []
        st_blocks: list[int] = []
        all_pcm = []
        for i in range(N_REPEAT):
            reset_hop()
            t0 = time.perf_counter()
            first_at = None
            pieces: list[bytes] = []
            n = 0
            for ch in cosy.inference_zero_shot(
                    text, "", str(REF_WAV), zero_shot_spk_id="elysia", stream=True):
                now = time.perf_counter() - t0
                if first_at is None:
                    first_at = now
                p = to_pcm16(ch["tts_speech"])
                if p:
                    pieces.append(p)
                    n += 1
            total = time.perf_counter() - t0
            if first_at is None:
                continue
            merged = b"".join(pieces)
            dur = len(merged) / 2 / SR
            st_first.append(first_at)
            st_total.append(total)
            st_dur.append(dur)
            st_n.append(n)
            if i == 0:
                st_blocks = [len(p) // 2 for p in pieces]
                all_pcm.append(merged)
                sf.write(str(OUT / f"{label}_stream.wav"),
                         np.frombuffer(merged, dtype="<i2"), SR, subtype="PCM_16")

        if not non_first or not st_first:
            print("  数据不足，跳过")
            continue

        mf_non = statistics.median(non_first)
        mf_st = statistics.median(st_first)
        improvement = (mf_non - mf_st) / mf_non * 100 if mf_non > 0 else 0

        print(f"  非流式: 首包 {mf_non:.2f}s  总耗时 {statistics.median(non_total):.2f}s  "
              f"音频 {statistics.median(non_dur):.2f}s")
        print(f"  流式  : 首包 {mf_st:.2f}s  总耗时 {statistics.median(st_total):.2f}s  "
              f"音频 {statistics.median(st_dur):.2f}s")
        print(f"  首包改善: {improvement:+.1f}%  "
              f"{'流式更优' if improvement > 5 else ('非流式更优' if improvement < -5 else '无明显差异')}")
        print(f"  分块: {int(statistics.median(st_n))} 块  "
              f"块大小(样本) {st_blocks if st_blocks else '—'}")

        # 爆音检测
        if all_pcm:
            click = detect_clicks(all_pcm[0], SR)
            print(f"  拼接处爆音检测: 最大跳变 {click['max_jump']:.3f}  "
                  f"超阈值样本 {click['clicks']} 个 "
                  f"{'（正常）' if click['clicks'] == 0 else '（需关注）'}")

        rows.append({
            "label": label, "chars": nc,
            "non_first": mf_non, "st_first": mf_st,
            "improvement": improvement,
            "blocks": int(statistics.median(st_n)),
            "dur": statistics.median(non_dur),
        })

    # ---- 汇总 ----
    print()
    print("=" * 104)
    print(" 汇总与路由建议")
    print("=" * 104)
    print(f"  {'文本':<14}{'字数':>6}{'非流式首包':>12}{'流式首包':>11}"
          f"{'改善':>9}{'块数':>6}  建议")
    print("  " + "-" * 76)

    for r in rows:
        if r["improvement"] > 15:
            advice = "用流式"
        elif r["improvement"] > 5:
            advice = "流式略优"
        else:
            advice = "用非流式"
        print(f"  {r['label']:<14}{r['chars']:>6}{r['non_first']:>11.2f}s"
              f"{r['st_first']:>10.2f}s{r['improvement']:>+8.1f}%"
              f"{r['blocks']:>6}  {advice}")

    # 阈值建议
    print()
    if len(rows) >= 2:
        positive = [r for r in rows if r["improvement"] > 5]
        negative = [r for r in rows if r["improvement"] <= 5]
        if positive and negative:
            lo = max((r["chars"] for r in negative), default=0)
            hi = min((r["chars"] for r in positive), default=0)
            print(f"  路由阈值建议：字符数 > {lo}（约 {hi}）时走流式，否则走非流式")
            print(f"  即阈值取 {lo}~{hi} 之间。建议用 {hi if hi else lo + 5}。")
        elif positive:
            print("  所有长度流式都更优 -> 统一走流式")
        else:
            print("  所有长度流式都不占优 -> 统一走非流式")

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
