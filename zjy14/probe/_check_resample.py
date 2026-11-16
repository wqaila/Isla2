"""检测参考音频是否被错误重采样（这是"音色不对+像机器人"的高概率根因）。

原理：
若原始音频是 16kHz，被当成 24kHz 使用（未重采样），则：
  - 所有频率内容被拉伸 1.5 倍（音高升高、音色变亮）
  - 12kHz 以上完全没有能量（因为原文件最高只有 8kHz）

若原始 32kHz 被当 24kHz：
  - 频率拉伸 1.333 倍
  - 16kHz 以上无能量

检测手段：
  1. 高频能量分布（判断是否存在"硬截止"）
  2. 频谱滚降点（rolloff）
  3. 与官方参考音频对比
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
OFFICIAL = Path("/home/user/CosyVoice/asset/zero_shot_prompt.wav")


def band_energy(x: np.ndarray, sr: int, lo: float, hi: float) -> float:
    """指定频段的能量占比（dB 相对总能量）。"""
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    total = spec.sum()
    if total <= 0:
        return -999.0
    band = spec[(freqs >= lo) & (freqs < hi)].sum()
    return 10 * np.log10(max(band / total, 1e-20))


def rolloff(x: np.ndarray, sr: int, pct: float = 0.99) -> float:
    """累积能量达到 pct 时的频率。"""
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1.0 / sr)
    cum = np.cumsum(spec)
    if cum[-1] <= 0:
        return 0.0
    idx = int(np.searchsorted(cum, cum[-1] * pct))
    return float(freqs[min(idx, len(freqs) - 1)])


def analyze(name: str, path: Path) -> None:
    x, sr = sf.read(str(path), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    print(f"\n{'='*72}")
    print(f" {name}")
    print(f" {path}")
    print(f"{'='*72}")
    print(f"  采样率 {sr}Hz  时长 {len(x)/sr:.2f}s  理论奈奎斯特 {sr//2}Hz")

    bands = [
        (0, 1000), (1000, 2000), (2000, 4000), (4000, 6000),
        (6000, 8000), (8000, 10000), (10000, 12000),
        (12000, 14000), (14000, 16000), (16000, 20000),
    ]
    print(f"\n  {'频段':<16}{'能量占比':>12}   {'柱状'}")
    print("  " + "-" * 60)
    for lo, hi in bands:
        if lo >= sr / 2:
            continue
        e = band_energy(x, sr, lo, min(hi, sr / 2))
        bar = "#" * max(0, int((e + 100) / 4)) if e > -100 else ""
        print(f"  {lo:>5}-{hi:<10}{e:>10.1f}dB   {bar}")

    print(f"\n  频谱滚降点:")
    for pct in (0.90, 0.95, 0.99, 0.999):
        print(f"    {pct*100:>5.1f}% 能量 -> {rolloff(x, sr, pct):>8.0f} Hz")

    # 检测硬截止：比较最高频段与邻近频段
    nyq = sr / 2
    top = band_energy(x, sr, nyq * 0.9, nyq)
    mid = band_energy(x, sr, nyq * 0.5, nyq * 0.6)
    print(f"\n  硬截止检测:")
    print(f"    中高频({nyq*0.5:.0f}-{nyq*0.6:.0f}Hz) 能量: {mid:.1f}dB")
    print(f"    最高频({nyq*0.9:.0f}-{nyq:.0f}Hz) 能量: {top:.1f}dB")
    print(f"    差值: {top - mid:+.1f}dB", end="")
    if top - mid < -35:
        print("  <<< 疑似硬截止（音频可能被错误上采样）")
    elif top - mid < -20:
        print("  < 高频衰减明显（可能是录音设备或压缩造成）")
    else:
        print("  正常")

    # 若存在硬截止，推算原始采样率
    r99 = rolloff(x, sr, 0.99)
    if r99 > 0:
        print(f"\n  若原始采样率为 X 且未重采样就当作 {sr}Hz 使用，")
        print(f"  则原始奈奎斯特约为 {r99:.0f}Hz -> 原始采样率约 {r99*2:.0f}Hz")


def main() -> int:
    analyze("爱莉希雅参考音频", REF)
    analyze("官方示例参考音频", OFFICIAL)

    print(f"\n{'='*72}")
    print(" 对照结论")
    print(f"{'='*72}")
    for name, p in (("爱莉希雅", REF), ("官方", OFFICIAL)):
        x, sr = sf.read(str(p), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        print(
            f"  {name:<10} 滚降99%={rolloff(x, sr):>6.0f}Hz  "
            f"8k以上能量={band_energy(x, sr, 8000, sr/2):>7.1f}dB"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
