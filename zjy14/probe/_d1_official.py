"""最后一块证据：官方 instruct2 路径（空 spk_id）是否有情绪效果？

已知：
  混合模式 W2：安慰剂效应（15.4Hz）大于真指令差异（10.1Hz）-> 情绪未生效
  官方 instruct2 + 空 spk：速度慢（3.68 字/秒 vs 正常 6.17）

本测试只问一件事：官方路径能否产生「方向正确」的情绪差异？
  同样用安慰剂对照，若官方路径的 happy/sad 差异显著大于安慰剂，
  说明官方路径有效（虽慢，但可作为备选）。

注意：官方 instruct2 + 空 spk_id 每次都要重编码参考音频（约 0.3s），
所以每组只跑 3 次以控制总时长。
"""
from __future__ import annotations

import sys
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
OUT = Path("/mnt/d/xiangmudaima/zjy14/_d1_official")
OUT.mkdir(parents=True, exist_ok=True)

SR = 24000
N = 3
TEXT = "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。"
PREFIX = "You are a helpful assistant.<|endofprompt|>"

CASES = {
    "O1_基线": "",
    "O2_安慰剂": PREFIX + "请用蓝色的语气说一句话。",
    "O3_开心": PREFIX + "请非常开心地说一句话。",
    "O4_伤心": PREFIX + "请非常伤心地说一句话。",
}


def voiced(x: np.ndarray, sr: int) -> tuple[float, float]:
    hop = int(sr * 0.01)
    n = len(x) // hop
    if n == 0:
        return 0.0, 1.0
    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    pk = float(rms.max())
    if pk <= 0:
        return 0.0, 1.0
    v = rms > max(pk * 0.02, 1e-6)
    idx = np.where(v)[0]
    if idx.size == 0:
        return 0.0, 1.0
    inner = v[idx[0]: idx[-1] + 1]
    return float(inner.sum()) * 0.01, float((~inner).mean())


def f0(x: np.ndarray, sr: int, fmin=70.0, fmax=450.0) -> tuple[float, float]:
    win, hop = int(sr * 0.04), int(sr * 0.01)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return 0.0, 0.0
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
    e = np.sqrt((fr**2).mean(axis=1))
    fr = fr[e >= np.percentile(e, 70)]
    if fr.shape[0] == 0:
        return 0.0, 0.0
    lo, hi = int(sr / fmax), int(sr / fmin)
    out = []
    for f in fr:
        f = f - f.mean()
        ac = np.correlate(f, f, mode="full")[len(f) - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        seg = ac[lo:min(hi + 1, len(ac))]
        if seg.size < 3:
            continue
        peaks = [i for i in range(1, len(seg) - 1)
                 if seg[i] > seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] > 0.35]
        if not peaks:
            continue
        best = max(peaks, key=lambda i: seg[i])
        half = best // 2
        if half >= lo and half < len(ac) and ac[half] > 0.5 * ac[lo + best]:
            best = half
        out.append(sr / (lo + best))
    if not out:
        return 0.0, 0.0
    a = np.array(out)
    return float(np.median(a)), float(np.std(a) / max(np.mean(a), 1e-9))


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3
    from custom_voice.inference.text_frontend import enable_wetext_frontend

    ref_body = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" in ref_body:
        ref_body = ref_body.split("<|endofprompt|>", 1)[1]
    ref_full = PREFIX + ref_body

    print("=" * 100)
    print(" 官方 instruct2 路径的情绪验证（空 spk_id，每次重编码参考）")
    print("=" * 100)

    cosy = CosyVoice3(str(MODEL), fp16=False)
    enable_wetext_frontend(cosy.frontend)
    cosy.add_zero_shot_spk(ref_full, str(REF_WAV), "elysia")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    reset_hop()
    list(cosy.inference_zero_shot("你好。", "", str(REF_WAV),
                                  zero_shot_spk_id="elysia", stream=False))

    results: dict[str, list[tuple[float, float, float]]] = {}

    for label, instr in CASES.items():
        print(f"\n--- {label} ---")
        vals = []
        for i in range(N):
            reset_hop()
            try:
                if not instr:
                    chunks = list(cosy.inference_zero_shot(
                        TEXT, "", str(REF_WAV), zero_shot_spk_id="elysia",
                        stream=False))
                else:
                    chunks = list(cosy.inference_instruct2(
                        TEXT, instr, str(REF_WAV), zero_shot_spk_id="",
                        stream=False))
            except Exception as exc:  # noqa: BLE001
                print(f"    第{i+1}次失败: {type(exc).__name__}: {str(exc)[:50]}")
                continue
            if not chunks:
                continue
            parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                     for c in chunks]
            y = np.concatenate(parts) if len(parts) > 1 else parts[0]
            sf.write(str(OUT / f"{label}_{i+1}.wav"), y, SR, subtype="PCM_16")
            dur = len(y) / SR
            vd, sil = voiced(y, SR)
            mf0, cv = f0(y, SR)
            vals.append((mf0, cv, dur))
            print(f"    第{i+1}次  F0 {mf0:6.1f}Hz  起伏 {cv:.3f}  "
                  f"{dur:.2f}s  静音 {sil:.0%}")
        if vals:
            results[label] = vals

    print()
    print("=" * 100)
    print(" 统计对比")
    print("=" * 100)
    print(f"  {'方案':<16}{'F0 中位':>10}{'F0 标准差':>11}"
          f"{'起伏中位':>10}{'时长中位':>10}")
    print("  " + "-" * 60)

    stats: dict[str, dict] = {}
    for label, vals in results.items():
        f0s = [v[0] for v in vals]
        cvs = [v[1] for v in vals]
        durs = [v[2] for v in vals]
        s = {
            "f0_med": float(np.median(f0s)),
            "f0_std": float(np.std(f0s)),
            "cv_med": float(np.median(cvs)),
            "dur_med": float(np.median(durs)),
        }
        stats[label] = s
        print(f"  {label:<16}{s['f0_med']:>10.1f}{s['f0_std']:>11.1f}"
              f"{s['cv_med']:>10.3f}{s['dur_med']:>9.2f}s")

    print()
    print("=" * 100)
    print(" 判定")
    print("=" * 100)

    base = stats.get("O1_基线")
    placebo = stats.get("O2_安慰剂")
    happy = stats.get("O3_开心")
    sad = stats.get("O4_伤心")

    if base and placebo and happy and sad:
        d_real = abs(happy["f0_med"] - sad["f0_med"])
        d_placebo = abs(placebo["f0_med"] - base["f0_med"])
        noise = max(base["f0_std"], placebo["f0_std"], 1.0)

        print(f"  基线 {base['f0_med']:.1f}Hz / 安慰剂 {placebo['f0_med']:.1f}Hz "
              f"/ 开心 {happy['f0_med']:.1f}Hz / 伤心 {sad['f0_med']:.1f}Hz")
        print(f"  开心 vs 伤心:  {d_real:.1f} Hz")
        print(f"  安慰剂 vs 基线: {d_placebo:.1f} Hz")
        print(f"  组内噪声:      {noise:.1f} Hz")
        print()
        if d_real > max(d_placebo * 2, noise * 2):
            print("  [结论] 官方路径的情绪差异显著 > 安慰剂效应 -> 有效")
        else:
            print("  [结论] 官方路径同样未超过安慰剂效应 -> 情绪指令无效")
        print(f"\n  时长对比：基线 {base['dur_med']:.2f}s vs 官方路径 "
              f"{happy['dur_med']:.2f}s（官方路径更慢）")

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
