"""验证修复方向：裁剪短片首尾静音后，克隆质量是否提升。

发现的问题链：
  1. 短片 1.0-1.7 有 22-34% 纯数字零（大量首尾静音 + 噪声门处理）
  2. 这导致参考音频里"有效语音"其实只有 5-6 秒，但文本按 8-9 秒写
  3. 且 HNR 很低（1.4 只有 -1.3dB，1.5 只有 3.2dB）—— 气声/噪声成分高

对照：
  T0 原短片（含静音）
  T1 裁掉首尾静音
  T2 裁剪 + 峰值归一化

同时测：合成文本长度与参考文本长度比的影响（已知比值 > 0.5 才好）。
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

MODEL = "/home/user/models/Fun-CosyVoice3-0.5B-2512"
CAND = Path("/mnt/d/xiangmudaima/zjy14/_ref_select")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_trim_test")
OUT.mkdir(parents=True, exist_ok=True)
SR = 24000
PREFIX = "You are a helpful assistant.<|endofprompt|>"

TEXTS = {
    "1.0": "早上好，新的一天从一场美妙的邂逅开始",
    "1.4": "这么晚了还不睡吗，是在想我对不对",
    "1.5": "睡不着的话就来聊聊天吧",
    "1.6": "晚上好，如此美妙的夜晚",
}

# 合成文本：刻意用较长文本使比值 > 0.5
SYNTH_LONG = (
    "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨，"
    "窗外的树叶被打得七零八落，今天总算放晴了。"
)


def frames(x, sr, win_s=0.04, hop_s=0.02):
    win, hop = int(sr * win_s), int(sr * hop_s)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return np.zeros((0, win))
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return x[idx].astype(np.float64)


def hnr(x, sr, fmin=70.0, fmax=450.0):
    fr = frames(x, sr)
    if fr.shape[0] == 0:
        return 0.0
    e = np.sqrt((fr**2).mean(axis=1))
    fr = fr[e >= np.percentile(e, 50)]
    lo, hi = int(sr / fmax), int(sr / fmin)
    vals = []
    for f in fr[:300]:
        f = f - f.mean()
        ac = np.correlate(f, f, mode="full")[len(f) - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:min(hi, len(ac))]
        if seg.size == 0:
            continue
        r = float(np.max(seg) / ac[0])
        if 0 < r < 1:
            vals.append(10 * np.log10(r / (1 - r)))
    return float(np.median(vals)) if vals else 0.0


def flatness(x, sr):
    fr = frames(x, sr)
    if fr.shape[0] == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(fr * np.hanning(fr.shape[1]), axis=1)) + 1e-12
    return float(np.median(np.exp(np.mean(np.log(spec), axis=1)) / np.mean(spec, axis=1)))


def centroid(x, sr):
    fr = frames(x, sr)
    if fr.shape[0] == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(fr * np.hanning(fr.shape[1]), axis=1))
    freqs = np.fft.rfftfreq(fr.shape[1], 1.0 / sr)
    mag = spec.sum(axis=0)
    return float((mag * freqs).sum() / mag.sum()) if mag.sum() > 0 else 0.0


def exact_zero_ratio(x, sr):
    hop = int(sr * 0.02)
    n = len(x) // hop
    if n == 0:
        return 0.0
    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    return float(np.mean(rms < 1e-9))


def describe(name, x, sr=SR):
    return {
        "name": name,
        "dur": round(len(x) / sr, 2),
        "flat": round(flatness(x, sr), 4),
        "hnr": round(hnr(x, sr), 1),
        "centroid": round(centroid(x, sr), 0),
        "zero": round(exact_zero_ratio(x, sr), 4),
    }


def print_row(d):
    print(
        f"  {d['name']:<32}{d['dur']:>6.2f}s  平坦{d['flat']:>7.4f}  "
        f"HNR{d['hnr']:>6.1f}dB  质心{d['centroid']:>6.0f}Hz  纯零{d['zero']:>6.2%}"
    )


def trim_silence(x, sr, rel_thr=0.02, pad_s=0.05):
    """裁掉首尾静音，保留少量余量。"""
    hop = int(sr * 0.01)
    n = len(x) // hop
    if n == 0:
        return x
    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return x
    voiced = rms > max(peak * rel_thr, 1e-6)
    idx = np.where(voiced)[0]
    if idx.size == 0:
        return x
    a = max(0, int(idx[0] * hop - pad_s * sr))
    b = min(len(x), int((idx[-1] + 1) * hop + pad_s * sr))
    return x[a:b]


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3

    print("=" * 112)
    print(" A. 裁剪前后参考音频特征")
    print("=" * 112)

    prepared = []
    for key, txt in TEXTS.items():
        p = CAND / f"cand_{key}.wav"
        if not p.is_file():
            continue
        x, sr = sf.read(str(p), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        print_row(describe(f"T0_{key}_原始", x))

        trimmed = trim_silence(x, sr)
        pt = OUT / f"trim_{key}.wav"
        sf.write(str(pt), trimmed, SR, subtype="PCM_16")
        print_row(describe(f"T1_{key}_裁静音", trimmed))

        peak = float(np.max(np.abs(trimmed)))
        if peak > 0:
            norm = (trimmed * (0.708 / peak)).astype(np.float32)
            pn = OUT / f"trim_norm_{key}.wav"
            sf.write(str(pn), norm, SR, subtype="PCM_16")
            print_row(describe(f"T2_{key}_裁+归一", norm))

        prepared.append((key, txt, pt, pn))
        print()

    print("=" * 112)
    print(" B. 合成对比（长文本，比值 > 0.5）")
    print("=" * 112)

    cosy = CosyVoice3(MODEL, fp16=False)
    print(f"  text_frontend = {cosy.frontend.text_frontend!r}")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    results = []

    def run(label, text, wav, prm):
        reset_hop()
        try:
            chunks = list(cosy.inference_zero_shot(
                text, prm, str(wav), zero_shot_spk_id="", stream=False))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{label}] 失败: {type(exc).__name__}: {exc}")
            return
        if not chunks:
            return
        parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze() for c in chunks]
        y = np.concatenate(parts) if len(parts) > 1 else parts[0]
        sf.write(str(OUT / f"{label}.wav"), y, SR, subtype="PCM_16")
        d = describe(label, y)
        results.append(d)
        print_row(d)
        print(f"      比值 {len(text)/len(prm):.2f}  块数 {len(chunks)}")

    for key, txt, pt, pn in prepared:
        prm = PREFIX + txt
        print(f"\n  --- {key}: {txt} ---")
        run(f"原_{key}", SYNTH_LONG, CAND / f"cand_{key}.wav", prm)
        run(f"裁_{key}", SYNTH_LONG, pt, prm)
        run(f"裁归_{key}", SYNTH_LONG, pn, prm)

    print()
    print("=" * 112)
    print(" C. 汇总（HNR 越高、平坦度越低、质心越接近参考越好）")
    print("=" * 112)
    print(f"  {'样本':<32}{'平坦度':>9}{'HNR':>9}{'质心':>9}")
    print("  " + "-" * 60)
    for d in sorted(results, key=lambda d: (-d["hnr"], d["flat"])):
        print(f"  {d['name']:<32}{d['flat']:>9.4f}{d['hnr']:>8.1f}dB{d['centroid']:>8.0f}Hz")

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
