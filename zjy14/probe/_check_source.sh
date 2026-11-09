#!/usr/bin/env bash
set -uo pipefail
source "${HOME}/cosyvoice-venv/bin/activate"
python - <<'PY'
import soundfile as sf
from pathlib import Path
import numpy as np

src = Path("/mnt/c/Users/user/Desktop/aili")
print("=== 源素材真实采样率 ===")
for p in sorted(src.glob("*.mp3")):
    try:
        info = sf.info(str(p))
        print(f"  {p.name:<12} {info.samplerate:>6}Hz  {info.channels}ch  "
              f"{info.duration:>8.2f}s  {info.format}/{info.subtype}")
    except Exception as exc:
        print(f"  {p.name:<12} 读取失败: {type(exc).__name__}: {exc}")

print()
print("=== 当前使用的参考音频 ===")
ref = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
info = sf.info(str(ref))
print(f"  ref_elysia.wav  {info.samplerate}Hz  {info.channels}ch  {info.duration:.2f}s")

print()
print("=== 从源文件直接解码，测基频 ===")
def f0_of(x, sr, fmin=60, fmax=500):
    win, hop = int(sr*0.04), int(sr*0.02)
    n = max(0, (len(x)-win)//hop + 1)
    if n <= 0: return np.zeros(0)
    idx = np.arange(win)[None,:] + hop*np.arange(n)[:,None]
    fr = x[idx].astype(np.float64)
    e = np.sqrt((fr**2).mean(axis=1))
    fr = fr[e >= np.percentile(e, 60)]
    lo, hi = int(sr/fmax), int(sr/fmin)
    out = []
    for f in fr:
        f = f - f.mean()
        ac = np.correlate(f, f, mode="full")[len(f)-1:]
        if ac[0] <= 0: continue
        seg = ac[lo:min(hi+1, len(ac))]
        if seg.size == 0: continue
        pk = int(np.argmax(seg)) + lo
        if ac[pk] < 0.3*ac[0]: continue
        out.append(sr/pk)
    return np.array(out)

# 1.5.mp3 (9.67s) 对应素材，用来对比
for name in ("1.5.mp3", "1.0.mp3"):
    p = src / name
    if not p.is_file(): continue
    x, sr = sf.read(str(p), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    f0 = f0_of(x, sr)
    print(f"  {name:<10} sr={sr}  F0中位={np.median(f0):.1f}Hz" if f0.size else f"  {name}: 无F0")

# 参考音频
x, sr = sf.read(str(ref), dtype="float32")
f0 = f0_of(x, sr)
print(f"  ref_elysia sr={sr}  F0中位={np.median(f0):.1f}Hz")

# 长合集 1.2.mp3 的 174.89-184.59 段（参考音频来源）
p = src / "1.2.mp3"
if p.is_file():
    x, sr = sf.read(str(p), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    seg = x[int(174.89*sr):int(184.59*sr)]
    f0 = f0_of(seg, sr)
    print(f"  1.2.mp3[174.89-184.59] sr={sr}  时长{len(seg)/sr:.2f}s  F0中位={np.median(f0):.1f}Hz" if f0.size else "  1.2.mp3 无F0")
PY
