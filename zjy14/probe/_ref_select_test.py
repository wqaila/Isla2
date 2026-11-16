"""验证"参考音频选段不当"是否是"像机器人/不像角色"的根因。

已知事实：
  - 21 分钟合集 F0 中位 317Hz，p10-p90 = 238-405Hz
  - 当前选段(174.89-184.59) F0 = 366Hz，处于分布偏高位置
  - 该段文本是"努力维持优雅美丽可爱…这可是身为副首领最重要的职责呀"
    —— 明显是亢奋/俏皮的表演腔，不是角色日常说话语气
  - 素材 8k+ 高频能量比官方示例高 11dB（偏亮）
  - 素材有 5.24% 纯数字零静音（经过噪声门处理）

对照设计（全部用同一句合成文本，只换参考）：
  R0  当前 ref_elysia.wav (9.7s, F0 366Hz, 亢奋段)
  R1  1.5.mp3 "睡不着的话就来聊聊天吧" (9.6s, F0 317Hz, 温柔场景)
  R2  1.4.mp3 "这么晚了还不睡吗" (7.7s, 偏低)
  R3  1.0.mp3 "早上好…" (9.4s)
  R4  从合集中挑 F0 接近 317Hz 的 8 秒段

评价指标：
  - F0 是否更接近角色的中位音高（317Hz）而非亢奋值（366Hz）
  - F0 起伏（F0CV）是否更接近自然语音（0.18-0.25）
  - 质心是否不过分偏亮
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
SRC = Path("/mnt/c/Users/user/Desktop/aili")
LONG = SRC / "1.2.mp3"
CUR_REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
PREFIX = "You are a helpful assistant.<|endofprompt|>"
OUT = Path("/mnt/d/xiangmudaima/zjy14/_ref_select")
OUT.mkdir(parents=True, exist_ok=True)

TEXT = "今天天气真不错，我们一起出去走走吧。"
SR = 24000

# 各短片的文本（来自 zjy13/docs/voice-elysia.md 的记录）
SHORT_CLIPS = {
    "1.0.mp3": "早上好，新的一天从一场美妙的邂逅开始",
    "1.1.mp3": "这么早起床是为了能早点见到我吗",
    "1.3.mp3": "快去休息了，约好了我们在梦里相见",
    "1.4.mp3": "这么晚了还不睡吗，是在想我对不对",
    "1.5.mp3": "睡不着的话就来聊聊天吧",
    "1.6.mp3": "晚上好，如此美妙的夜晚",
    "1.7.mp3": "月亮升起来了，要陪我一起去看星星吗",
}


# ---------------------------------------------------------------- 特征


def frame_signal(x, sr, win_s=0.04, hop_s=0.02):
    win, hop = int(sr * win_s), int(sr * hop_s)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return np.zeros((0, win))
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return x[idx].astype(np.float64)


def f0_series(x, sr, fmin=70.0, fmax=450.0):
    frames = frame_signal(x, sr)
    if frames.shape[0] == 0:
        return np.zeros(0)
    e = np.sqrt((frames**2).mean(axis=1))
    frames = frames[e >= np.percentile(e, 70)]
    if frames.shape[0] == 0:
        return np.zeros(0)
    lo, hi = int(sr / fmax), int(sr / fmin)
    out = []
    for fr in frames:
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, mode="full")[len(fr) - 1:]
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
    return np.array(out)


def centroid(x, sr):
    frames = frame_signal(x, sr)
    if frames.shape[0] == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(frames * np.hanning(frames.shape[1]), axis=1))
    freqs = np.fft.rfftfreq(frames.shape[1], 1.0 / sr)
    mag = spec.sum(axis=0)
    return float((mag * freqs).sum() / mag.sum()) if mag.sum() > 0 else 0.0


def describe(name, x, sr=SR):
    f0 = f0_series(x, sr)
    return {
        "name": name,
        "dur": round(len(x) / sr, 2),
        "f0": round(float(np.median(f0)), 1) if f0.size else 0.0,
        "f0cv": round(float(np.std(f0) / max(np.mean(f0), 1e-9)), 3) if f0.size else 0.0,
        "centroid": round(centroid(x, sr), 0),
    }


def print_row(d, tag=""):
    print(
        f"  {d['name']:<28}{d['dur']:>6.2f}s  F0{d['f0']:>7.1f}Hz"
        f"  起伏{d['f0cv']:>6.3f}  质心{d['centroid']:>6.0f}Hz{tag}"
    )


# ---------------------------------------------------------------- 主流程


def pick_low_f0_segment(mono, sr, target_f0=317.0, win_s=8.0):
    """从长音频里挑 F0 最接近目标值的 8 秒窗口。"""
    win = int(win_s * sr)
    step = int(2.0 * sr)
    best = None
    for start in range(0, len(mono) - win, step):
        seg = mono[start:start + win]
        rms = np.sqrt((seg.astype(np.float64) ** 2).mean())
        if rms < 0.01:
            continue
        f0 = f0_series(seg, sr)
        if f0.size < 20:
            continue
        med = float(np.median(f0))
        # 目标：F0 接近 317，且有声帧多（说明连续说话）
        score = abs(med - target_f0) - f0.size * 0.01
        if best is None or score < best[0]:
            best = (score, start, med, seg.copy())
    return best


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3

    print("=" * 96)
    print(" 参考音频候选")
    print("=" * 96)

    candidates: list[tuple[str, Path, str]] = []

    # R0 当前
    cur_text = Path(
        "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt"
    ).read_text(encoding="utf-8").strip()
    candidates.append(("R0_当前参考", CUR_REF, cur_text))
    x, _ = sf.read(str(CUR_REF), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    print_row(describe("R0_当前(9.7s)", x))

    # R1-R4 短片
    for fn, txt in SHORT_CLIPS.items():
        p = SRC / fn
        if not p.is_file():
            continue
        x, sr = sf.read(str(p), dtype="float32", always_2d=True)
        x = x.mean(axis=1)
        # 转 24k
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(sr, SR)
        y = resample_poly(x.astype(np.float64), SR // g, sr // g).astype(np.float32)
        out = OUT / f"cand_{p.stem}.wav"
        sf.write(str(out), y, SR, subtype="PCM_16")
        d = describe(f"R_{fn}", y)
        print_row(d)
        if fn in ("1.4.mp3", "1.5.mp3", "1.0.mp3"):
            candidates.append((f"R_{fn}", out, txt))

    # R5 从合集挑低音高段
    print("\n  从 21 分钟合集中挑 F0 接近 317Hz 的 8 秒段...")
    long_x, long_sr = sf.read(str(LONG), dtype="float32", always_2d=True)
    long_mono = long_x.mean(axis=1)
    picked = pick_low_f0_segment(long_mono, long_sr, target_f0=317.0, win_s=8.0)
    if picked:
        _, start, med, seg = picked
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(long_sr, SR)
        y = resample_poly(seg.astype(np.float64), SR // g, long_sr // g).astype(np.float32)
        out = OUT / "cand_lowf0.wav"
        sf.write(str(out), y, SR, subtype="PCM_16")
        d = describe(f"R5_低音高段({start/long_sr:.0f}s)", y)
        print_row(d)
        # 该段文本未知，用 ASR 不现实，跳过合成（避免文本错位导致误判）
        print(f"        （该段起始 {start/long_sr:.1f}s，文本未知，不参与合成对比）")

    print()
    print("=" * 96)
    print(" 合成对比（同一句文本，只换参考音频）")
    print("=" * 96)

    cosy = CosyVoice3(MODEL, fp16=False)
    print(f"  text_frontend = {cosy.frontend.text_frontend!r}")
    results = []

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    for label, wav_path, txt in candidates:
        prompt = txt if "<|endofprompt|>" in txt else PREFIX + txt
        reset_hop()
        try:
            chunks = list(cosy.inference_zero_shot(
                TEXT, prompt, str(wav_path), zero_shot_spk_id="", stream=False))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{label}] 失败: {type(exc).__name__}: {exc}")
            continue
        if not chunks:
            print(f"  [{label}] 无输出")
            continue
        y = chunks[0]["tts_speech"].detach().cpu().float().numpy().squeeze()
        sf.write(str(OUT / f"synth_{label}.wav"), y, SR, subtype="PCM_16")
        d = describe(label, y)
        results.append(d)
        print_row(d)

    print()
    print("=" * 96)
    print(" 汇总")
    print("=" * 96)
    print(f"  {'样本':<28}{'F0':>9}{'起伏':>8}{'质心':>9}   评价")
    print("  " + "-" * 74)
    for d in results:
        notes = []
        if abs(d["f0"] - 317) < 30:
            notes.append("F0接近角色中位")
        if d["f0cv"] > 0.15:
            notes.append("有起伏")
        if d["centroid"] < 3000:
            notes.append("不过亮")
        print(
            f"  {d['name']:<28}{d['f0']:>9.1f}{d['f0cv']:>8.3f}"
            f"{d['centroid']:>9.0f}   {', '.join(notes)}"
        )

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
