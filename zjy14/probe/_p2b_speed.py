"""单独验证 speed 参数（排除其他逻辑干扰）。"""
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
OUT = Path("/mnt/d/xiangmudaima/zjy14/_p2b_speed2")
OUT.mkdir(parents=True, exist_ok=True)

SR = 24000
PREFIX = "You are a helpful assistant.<|endofprompt|>"
TEXT = "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。"
SPEEDS = [0.8, 0.9, 1.0, 1.1, 1.2]
N = 3


def count_chars(t: str) -> int:
    return len([c for c in t if c not in "，。！？、；：,.!?;: \n\t"])


def voiced(x: np.ndarray, sr: int) -> float:
    hop = int(sr * 0.01)
    n = len(x) // hop
    if n == 0:
        return 0.0
    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    pk = float(rms.max())
    if pk <= 0:
        return 0.0
    v = rms > max(pk * 0.02, 1e-6)
    idx = np.where(v)[0]
    if idx.size == 0:
        return 0.0
    return float(v[idx[0]: idx[-1] + 1].sum()) * 0.01


def f0(x: np.ndarray, sr: int, fmin=70.0, fmax=450.0) -> float:
    win, hop = int(sr * 0.04), int(sr * 0.01)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return 0.0
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
    e = np.sqrt((fr**2).mean(axis=1))
    fr = fr[e >= np.percentile(e, 70)]
    if fr.shape[0] == 0:
        return 0.0
    lo, hi = int(sr / fmax), int(sr / fmin)
    out = []
    for f in fr:
        f = f - f.mean()
        ac = np.correlate(f, f, mode="full")[len(f) - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:min(hi + 1, len(ac))]
        if seg.size == 0:
            continue
        pk = int(np.argmax(seg)) + lo
        if ac[pk] < 0.3 * ac[0]:
            continue
        out.append(sr / pk)
    return float(np.median(out)) if out else 0.0


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3
    from custom_voice.inference.text_frontend import enable_wetext_frontend

    ref_body = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" in ref_body:
        ref_body = ref_body.split("<|endofprompt|>", 1)[1]
    ref_full = PREFIX + ref_body

    print("=" * 80)
    print(" speed 参数验证")
    print("=" * 80)

    cosy = CosyVoice3(str(MODEL), fp16=False)
    enable_wetext_frontend(cosy.frontend)
    cosy.add_zero_shot_spk(ref_full, str(REF_WAV), "elysia")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    reset_hop()
    list(cosy.inference_zero_shot("你好。", "", str(REF_WAV),
                                  zero_shot_spk_id="elysia", stream=False))

    nc = count_chars(TEXT)
    rows = []

    for sp in SPEEDS:
        durs, rates, f0s = [], [], []
        for i in range(N):
            reset_hop()
            try:
                chunks = list(cosy.inference_zero_shot(
                    TEXT, "", str(REF_WAV), zero_shot_spk_id="elysia",
                    stream=False, speed=sp))
            except Exception as exc:  # noqa: BLE001
                print(f"  speed={sp} 第{i+1}次失败: {type(exc).__name__}: {exc}")
                continue
            if not chunks:
                print(f"  speed={sp} 第{i+1}次无输出")
                continue
            parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                     for c in chunks]
            y = np.concatenate(parts) if len(parts) > 1 else parts[0]
            sf.write(str(OUT / f"sp{sp}_{i+1}.wav"), y, SR, subtype="PCM_16")
            dur = len(y) / SR
            vd = voiced(y, SR)
            durs.append(dur)
            rates.append(nc / max(vd, 0.01))
            f0s.append(f0(y, SR))

        if durs:
            rows.append({
                "speed": sp,
                "dur": float(np.median(durs)),
                "rate": float(np.median(rates)),
                "f0": float(np.median(f0s)),
                "n": len(durs),
            })

    print()
    print(f"  {'speed':>7}{'时长':>9}{'语速':>9}{'F0':>9}{'样本':>6}  相对 1.0")
    print("  " + "-" * 56)

    base = next((r for r in rows if abs(r["speed"] - 1.0) < 1e-6), None)
    for r in rows:
        rel = f"{(r['dur']/base['dur'] - 1)*100:+.1f}%" if base else "-"
        print(f"  {r['speed']:>7.1f}{r['dur']:>8.2f}s{r['rate']:>9.2f}"
              f"{r['f0']:>9.1f}{r['n']:>6}  {rel}")

    print()
    if len(rows) >= 2:
        lo = min(rows, key=lambda r: r["dur"])
        hi = max(rows, key=lambda r: r["dur"])
        span = (hi["dur"] / lo["dur"] - 1) * 100
        f0span = max(r["f0"] for r in rows) - min(r["f0"] for r in rows)
        print(f"  时长跨度: {lo['dur']:.2f}s (speed={lo['speed']}) -> "
              f"{hi['dur']:.2f}s (speed={hi['speed']})  = {span:.0f}%")
        print(f"  F0 跨度: {f0span:.1f} Hz")
        print(f"  判定: {'speed 有效可用' if span > 20 else 'speed 效果有限'}")
        print(f"        音色{'稳定' if f0span < 15 else '有变化，需注意'}")

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
