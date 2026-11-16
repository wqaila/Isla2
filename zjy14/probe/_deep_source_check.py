"""深挖源素材质量：判断"像机器人"是素材问题还是模型问题。

检查三件事：
  A. 源 MP3 的频谱与静音特征（是否经过噪声门/压缩等处理）
  B. 21 分钟合集内部的一致性（是否混入了其他说话人）
  C. 当前选段（174.89-184.59）是否为异常段
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

SRC = Path("/mnt/c/Users/user/Desktop/aili")
LONG = SRC / "1.2.mp3"
REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")


def frame_rms(x, sr, hop_s=0.02):
    hop = max(1, int(sr * hop_s))
    n = len(x) // hop
    if n == 0:
        return np.zeros(1)
    return np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))


def f0_robust(x, sr, fmin=70.0, fmax=450.0):
    """稳健基频估计：用倒谱+自相关双验证，减少倍频误判。"""
    win, hop = int(sr * 0.05), int(sr * 0.025)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return np.zeros(0)
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
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
        # 找局部极大值，取第一个超过 0.5 的（避免选到倍频的低谷）
        peaks = []
        for i in range(1, len(seg) - 1):
            if seg[i] > seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] > 0.35:
                peaks.append(i)
        if not peaks:
            continue
        # 取最高的峰，但若存在明显更高的次低频峰（基频），优先它
        best = max(peaks, key=lambda i: seg[i])
        # 检查 best/2 处是否有接近的峰（说明 best 是倍频）
        half = best // 2
        if half >= lo and half < len(ac) and ac[half] > 0.5 * ac[lo + best]:
            best = half
        out.append(sr / (lo + best))
    return np.array(out)


def band_ratio(x, sr, lo, hi):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    tot = spec.sum()
    if tot <= 0:
        return -999.0
    return 10 * np.log10(max(spec[(freqs >= lo) & (freqs < hi)].sum() / tot, 1e-20))


def main() -> int:
    print("=" * 90)
    print(" A. 源 MP3 质量特征")
    print("=" * 90)

    x, sr = sf.read(str(LONG), dtype="float32", always_2d=True)
    print(f"  1.2.mp3  {sr}Hz  {x.shape[1]}ch  {len(x)/sr:.1f}s")
    if x.shape[1] > 1:
        # 检查左右声道差异
        l, r = x[:, 0], x[:, 1]
        diff = np.abs(l - r).mean() / max(np.abs(l).mean(), 1e-9)
        corr = float(np.corrcoef(l, r)[0, 1])
        print(f"  左右声道: 相关系数={corr:.4f}  平均差异比={diff:.4f}")
        print(f"  -> {'基本是单声道复制' if corr > 0.99 else '立体声内容有差异（可能含混响/分离）'}")
    mono = x.mean(axis=1)

    # 静音特征
    rms = frame_rms(mono, sr)
    zeros = np.mean(rms < 1e-9)
    print(f"\n  静音帧（RMS < 1e-9）占比: {zeros:.2%}")
    if zeros > 0.01:
        print("  <<< 存在纯数字零静音，说明素材经过噪声门或强处理")
    print(f"  帧 RMS 分布: p10={np.percentile(rms,10):.2e}  "
          f"p50={np.percentile(rms,50):.2e}  p90={np.percentile(rms,90):.2e}")

    # 频谱
    print(f"\n  频谱分布:")
    for lo, hi in [(0,500),(500,1000),(1000,2000),(2000,4000),
                   (4000,6000),(6000,8000),(8000,10000),(10000,14000),(14000,20000)]:
        if lo >= sr/2:
            continue
        print(f"    {lo:>5}-{hi:<6} {band_ratio(mono, sr, lo, min(hi, sr/2)):>8.1f}dB")

    # 削波
    peak = float(np.max(np.abs(mono)))
    clip = float(np.mean(np.abs(mono) >= 0.99))
    print(f"\n  峰值 {peak:.4f}  削波比例 {clip:.4%}")

    print()
    print("=" * 90)
    print(" B. 21 分钟合集内部一致性（是否混入其他说话人）")
    print("=" * 90)

    # 按 30 秒窗口采样分析
    win_s = 30.0
    n_win = int(len(mono) / sr / win_s)
    print(f"  按 {win_s}s 窗口扫描 {n_win} 个窗口：")
    print(f"  {'起点':>8}{'F0中位':>9}{'F0帧数':>8}{'质心':>9}{'平坦度':>9}{'有声比':>8}")
    print("  " + "-" * 52)

    stats = []
    for i in range(0, n_win, max(1, n_win // 40)):  # 最多 40 个采样点
        a = int(i * win_s * sr)
        b = min(int((i + 1) * win_s * sr), len(mono))
        seg = mono[a:b]
        if len(seg) < sr:
            continue
        f0 = f0_robust(seg, sr)
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        freqs = np.fft.rfftfreq(len(seg), 1.0 / sr)
        cen = float((spec * freqs).sum() / spec.sum()) if spec.sum() > 0 else 0
        sp = spec + 1e-12
        flat = float(np.exp(np.mean(np.log(sp))) / np.mean(sp))
        seg_rms = frame_rms(seg, sr)
        voiced = float(np.mean(seg_rms > np.percentile(seg_rms, 30)))
        if f0.size:
            stats.append((f0, cen, flat))
            print(f"  {i*win_s:>7.0f}s{np.median(f0):>9.1f}{f0.size:>8}"
                  f"{cen:>9.0f}{flat:>9.4f}{voiced:>8.2f}")

    if stats:
        allf0 = np.concatenate([s[0] for s in stats])
        cens = np.array([s[1] for s in stats])
        print(f"\n  全局 F0: 中位 {np.median(allf0):.1f}Hz  "
              f"p10 {np.percentile(allf0,10):.1f}  p90 {np.percentile(allf0,90):.1f}")
        print(f"  窗口质心: 中位 {np.median(cens):.0f}Hz  "
              f"范围 {cens.min():.0f}-{cens.max():.0f}Hz  标准差 {cens.std():.0f}")
        print(f"  -> 质心变异系数 {cens.std()/max(cens.mean(),1e-9):.3f}")
        if cens.std() / max(cens.mean(), 1e-9) > 0.25:
            print("  <<< 窗口间音色差异大，可能存在多个说话人或音乐段")
        else:
            print("  音色相对一致")

    print()
    print("=" * 90)
    print(" C. 当前选段 vs 其他段落")
    print("=" * 90)

    ref, _ = sf.read(str(REF), dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    f0r = f0_robust(ref, 24000)
    spec_r = np.abs(np.fft.rfft(ref * np.hanning(len(ref))))
    freqs_r = np.fft.rfftfreq(len(ref), 1.0 / 24000)
    cen_r = float((spec_r * freqs_r).sum() / spec_r.sum())
    print(f"  当前选段(174.89-184.59): F0中位 {np.median(f0r):.1f}Hz  质心 {cen_r:.0f}Hz  "
          f"8k+ {band_ratio(ref, 24000, 8000, 12000):.1f}dB")

    # 从源文件其他位置取几段对比（44.1kHz 直接分析，避免重采样影响）
    print(f"\n  源文件其他段落（44.1kHz 原始）:")
    for start_s in (60, 300, 600, 900, 1200):
        a = int(start_s * sr)
        b = min(a + int(10 * sr), len(mono))
        seg = mono[a:b]
        if len(seg) < sr * 3:
            continue
        f0 = f0_robust(seg, sr)
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        freqs = np.fft.rfftfreq(len(seg), 1.0 / sr)
        cen = float((spec * freqs).sum() / spec.sum()) if spec.sum() > 0 else 0
        if f0.size:
            print(f"    {start_s:>5}s: F0中位 {np.median(f0):>6.1f}Hz  质心 {cen:>6.0f}Hz  "
                  f"8k+ {band_ratio(seg, sr, 8000, 14000):>7.1f}dB")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
