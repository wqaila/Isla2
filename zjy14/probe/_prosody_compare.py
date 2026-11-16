"""补全文本后，用韵律指标衡量改善（相似度只测身份，不测语气）。

用户反馈"不够"，但说话人相似度已 0.92。说明问题在韵律维度。

对比指标（全部与参考音频比）：
  F0 分布      - 音高范围是否匹配
  F0 起伏(F0CV) - 语调变化幅度（平淡 vs 有起伏）
  能量包络变异  - 说话轻重变化
  语速(字/秒)   - 节奏快慢
  停顿结构      - 停顿位置与长度
  音节时长变异  - 每个字的时长是否自然（机械感的核心指标）
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

REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_fixed_text")
CAND = Path("/mnt/d/xiangmudaima/zjy14/_ref_select")
TRIM = Path("/mnt/d/xiangmudaima/zjy14/_trim_test")
SR = 24000


def frames(x, sr, win_s=0.04, hop_s=0.01):
    win, hop = int(sr * win_s), int(sr * hop_s)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return np.zeros((0, win)), hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return x[idx].astype(np.float64), hop


def f0_series(x, sr, fmin=70.0, fmax=450.0):
    fr, _ = frames(x, sr)
    if fr.shape[0] == 0:
        return np.zeros(0)
    e = np.sqrt((fr**2).mean(axis=1))
    fr = fr[e >= np.percentile(e, 70)]
    if fr.shape[0] == 0:
        return np.zeros(0)
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
    return np.array(out)


def energy_envelope(x, sr):
    fr, _ = frames(x, sr)
    if fr.shape[0] == 0:
        return np.zeros(0)
    return np.sqrt((fr**2).mean(axis=1))


def pause_profile(x, sr, rel_thr=0.02, min_pause_s=0.08):
    """返回 (停顿数, 平均停顿ms, 静音占比)。"""
    fr, hop = frames(x, sr)
    if fr.shape[0] == 0:
        return 0, 0.0, 0.0
    e = np.sqrt((fr**2).mean(axis=1))
    peak = float(e.max())
    if peak <= 0:
        return 0, 0.0, 0.0
    silent = e < max(peak * rel_thr, 1e-6)
    idx = np.where(~silent)[0]
    if idx.size == 0:
        return 0, 0.0, 1.0
    inner = silent[idx[0]: idx[-1] + 1]
    pauses = []
    i = 0
    while i < len(inner):
        if inner[i]:
            j = i
            while j < len(inner) and inner[j]:
                j += 1
            d = (j - i) * hop / sr
            if d >= min_pause_s:
                pauses.append(d)
            i = j
        else:
            i += 1
    return len(pauses), (float(np.mean(pauses)) * 1000 if pauses else 0.0), float(inner.mean())


def syllable_variability(x, sr):
    """音节时长变异系数：把有声段按能量谷切分成"音节"，算时长标准差/均值。

    这是"机械感"的核心指标：真人说话音节长短差异大（CV 0.3-0.6），
    合成语音如果 CV 很低（<0.2），听起来就像节拍器，机械。
    """
    fr, hop = frames(x, sr)
    if fr.shape[0] < 10:
        return 0.0, 0
    e = np.sqrt((fr**2).mean(axis=1))
    peak = float(e.max())
    if peak <= 0:
        return 0.0, 0
    voiced = e > max(peak * 0.05, 1e-6)
    idx = np.where(voiced)[0]
    if idx.size == 0:
        return 0.0, 0
    seg = voiced[idx[0]: idx[-1] + 1]

    # 切分：连续有声段视为一个"音节块"
    lengths = []
    i = 0
    while i < len(seg):
        if seg[i]:
            j = i
            while j < len(seg) and seg[j]:
                j += 1
            lengths.append((j - i) * hop / sr)
            i = j
        else:
            i += 1

    if len(lengths) < 3:
        return 0.0, len(lengths)
    arr = np.array(lengths)
    return float(arr.std() / max(arr.mean(), 1e-9)), len(lengths)


def describe(name, x, sr=SR):
    f0 = f0_series(x, sr)
    env = energy_envelope(x, sr)
    npause, pms, sratio = pause_profile(x, sr)
    sCV, nseg = syllable_variability(x, sr)

    f0cv = float(np.std(f0) / max(np.mean(f0), 1e-9)) if f0.size else 0.0
    # 能量包络变异（去掉静音段）
    if env.size:
        voiced_env = env[env > env.max() * 0.05]
        env_cv = float(voiced_env.std() / max(voiced_env.mean(), 1e-9)) if voiced_env.size else 0.0
    else:
        env_cv = 0.0

    return {
        "name": name,
        "dur": round(len(x) / sr, 2),
        "f0": round(float(np.median(f0)), 1) if f0.size else 0.0,
        "f0_p10": round(float(np.percentile(f0, 10)), 1) if f0.size else 0.0,
        "f0_p90": round(float(np.percentile(f0, 90)), 1) if f0.size else 0.0,
        "f0cv": round(f0cv, 3),
        "env_cv": round(env_cv, 3),
        "pauses": npause,
        "pause_ms": round(pms, 0),
        "silence": round(sratio, 3),
        "syl_cv": round(sCV, 3),
        "syl_n": nseg,
    }


def print_row(d):
    print(
        f"  {d['name']:<18}{d['dur']:>6.2f}s  F0{d['f0']:>6.1f}"
        f"[{d['f0_p10']:.0f}-{d['f0_p90']:.0f}]  "
        f"F0起伏{d['f0cv']:>5.3f}  能量起伏{d['env_cv']:>5.3f}  "
        f"音节CV{d['syl_cv']:>5.3f}({d['syl_n']:>3})  "
        f"停顿{d['pauses']:>2}/{d['pause_ms']:>4.0f}ms"
    )


def main() -> int:
    print("=" * 130)
    print(" 韵律指标对比：参考 vs 合成（补全文本前后）")
    print("=" * 130)
    print("  音节CV = 音节时长变异系数。真人 0.3-0.6，越低越机械")
    print("  F0起伏 = 语调变化幅度。真人 0.15-0.30，越低越平淡")
    print()

    # 参考
    ref, _ = sf.read(str(REF), dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    ref_d = describe("当前参考", ref)
    print_row(ref_d)

    # 候选参考
    for key in ("1.5", "1.6"):
        p = TRIM / f"trim_{key}.wav"
        if p.is_file():
            x, sr = sf.read(str(p), dtype="float32")
            if x.ndim > 1:
                x = x.mean(axis=1)
            print_row(describe(f"{key}参考(裁)", x, sr))

    print()
    print("-" * 130)
    print(" 合成结果")
    print("-" * 130)

    synths = []
    for label, p in [
        ("1.5_补全前", OUT / "1.5_补全前.wav"),
        ("1.5_补全后", OUT / "1.5_补全后.wav"),
        ("1.6_补全前", OUT / "1.6_补全前.wav"),
        ("1.6_补全后", OUT / "1.6_补全后.wav"),
    ]:
        if not p.is_file():
            continue
        x, sr = sf.read(str(p), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        d = describe(label, x, sr)
        synths.append(d)
        print_row(d)

    # 也对比当前参考的合成（0.9208 那个）
    p = Path("/mnt/d/xiangmudaima/zjy14/_ratio_test/B_长参考+长文本.wav")
    if p.is_file():
        x, sr = sf.read(str(p), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        d = describe("当前参考合成", x, sr)
        synths.append(d)
        print_row(d)

    print()
    print("=" * 130)
    print(" 与参考的韵律差距（越小越好）")
    print("=" * 130)
    print(f"  {'样本':<18}{'F0差':>8}{'F0起伏差':>10}{'能量起伏差':>11}"
          f"{'音节CV差':>10}{'停顿差':>8}")
    print("  " + "-" * 70)
    for d in synths:
        print(
            f"  {d['name']:<18}{d['f0'] - ref_d['f0']:>+8.1f}"
            f"{d['f0cv'] - ref_d['f0cv']:>+10.3f}"
            f"{d['env_cv'] - ref_d['env_cv']:>+11.3f}"
            f"{d['syl_cv'] - ref_d['syl_cv']:>+10.3f}"
            f"{d['pauses'] - ref_d['pauses']:>+8}"
        )

    print()
    print("=" * 130)
    print(" 解读")
    print("=" * 130)
    print("  若「音节CV」明显低于参考 -> 机械感来源（节奏太均匀）")
    print("  若「F0起伏」明显低于参考 -> 平淡感来源（语调没变化）")
    print("  若「能量起伏」明显低于参考 -> 缺乏轻重变化")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
