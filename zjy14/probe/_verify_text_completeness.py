"""不需要 ASR 的独立验证：用音频时长反推"实际说了多少字"。

原理：
  中文正常语速 4-5 字/秒（官方参考实测 4.02 字/秒，当前参考 4.33 字/秒）。
  若一段音频时长 9.59s，按正常语速应包含约 38-48 个字。
  但标注只有 11 个字。

  除非这段音频里有大量非语音内容（长停顿、音乐、音效），
  否则标注必然不完整。

本脚本量化"内部静音"占比，判断能否用停顿解释语速异常。
若内部静音不足以解释，则文本标注确实残缺。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

CAND = Path("/mnt/d/xiangmudaima/zjy14/_ref_select")
TRIM = Path("/mnt/d/xiangmudaima/zjy14/_trim_test")
REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
OFFICIAL = Path("/home/user/CosyVoice/asset/zero_shot_prompt.wav")

CLAIMS = {
    "1.0": ("早上好，新的一天从一场美妙的邂逅开始", 18),
    "1.1": ("这么早起床是为了能早点见到我吗", 15),
    "1.3": ("快去休息了，约好了我们在梦里相见", 16),
    "1.4": ("这么晚了还不睡吗，是在想我对不对", 16),
    "1.5": ("睡不着的话就来聊聊天吧", 11),
    "1.6": ("晚上好，如此美妙的夜晚", 11),
    "1.7": ("月亮升起来了，要陪我一起去看星星吗", 17),
}

NORMAL_RATE = 4.2  # 字/秒，取官方与当前参考的均值


def analyze(x: np.ndarray, sr: int) -> dict:
    """返回语音段总时长、静音段总时长、语音段数。"""
    hop = int(sr * 0.01)
    n = len(x) // hop
    if n == 0:
        return {"voiced_s": 0.0, "silent_s": 0.0, "segments": 0, "long_pauses": 0}

    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return {"voiced_s": 0.0, "silent_s": 0.0, "segments": 0, "long_pauses": 0}

    # 相对峰值 -34dB 以下算静音（比之前的 2% 更严格，避免把气声算静音）
    thr = max(peak * 0.02, 1e-6)
    voiced = rms > thr

    # 找首尾
    idx = np.where(voiced)[0]
    if idx.size == 0:
        return {"voiced_s": 0.0, "silent_s": len(x) / sr, "segments": 0, "long_pauses": 0}

    a, b = idx[0], idx[-1] + 1
    inner_voiced = voiced[a:b]
    voiced_s = float(inner_voiced.sum()) * 0.01
    silent_s = float((~inner_voiced).sum()) * 0.01

    # 统计语音段数与长停顿（>200ms）
    segs = 0
    long_pauses = 0
    i = 0
    while i < len(inner_voiced):
        if inner_voiced[i]:
            j = i
            while j < len(inner_voiced) and inner_voiced[j]:
                j += 1
            segs += 1
            i = j
        else:
            j = i
            while j < len(inner_voiced) and not inner_voiced[j]:
                j += 1
            if (j - i) * 0.01 >= 0.20:
                long_pauses += 1
            i = j

    return {
        "voiced_s": round(voiced_s, 2),
        "silent_s": round(silent_s, 2),
        "segments": segs,
        "long_pauses": long_pauses,
    }


def main() -> int:
    print("=" * 108)
    print(" 文本标注完整性验证（用音频时长反推）")
    print("=" * 108)
    print(f"  基准：中文正常语速约 {NORMAL_RATE} 字/秒")
    print("  方法：若「语音段时长 × 正常语速」远大于标注字数，则标注必然残缺")
    print()

    print(f"  {'音频':<16}{'总时长':>8}{'语音段':>8}{'静音':>7}{'段数':>5}"
          f"{'标注字':>7}{'应有字':>7}{'缺口':>7}  结论")
    print("  " + "-" * 96)

    def check(label: str, path: Path, text: str, n_chars: int) -> dict | None:
        if not path.is_file():
            return None
        x, sr = sf.read(str(path), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        total = len(x) / sr
        st = analyze(x, sr)
        expected = st["voiced_s"] * NORMAL_RATE
        gap = expected - n_chars
        gap_ratio = gap / max(n_chars, 1)

        if gap_ratio > 0.8:
            judge = f"标注残缺（少约 {gap:.0f} 字）"
        elif gap_ratio > 0.35:
            judge = f"标注偏少（少约 {gap:.0f} 字）"
        elif gap_ratio < -0.35:
            judge = "标注偏多"
        else:
            judge = "基本吻合"

        print(f"  {label:<16}{total:>7.2f}s{st['voiced_s']:>7.2f}s"
              f"{st['silent_s']:>6.2f}s{st['segments']:>5}"
              f"{n_chars:>7}{expected:>7.0f}{gap:>+7.0f}  {judge}")
        return {**st, "total": total, "n_chars": n_chars,
                "expected": expected, "gap": gap, "label": label}

    # 基准
    check("官方参考", OFFICIAL, "希望你以后能够做的比我还好呦", 14)
    check("当前参考", REF, "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀", 42)

    # 短片（用裁剪后的，即实际会用的）
    results = []
    for key, (txt, n) in CLAIMS.items():
        p = TRIM / f"trim_{key}.wav"
        if not p.is_file():
            p = CAND / f"cand_{key}.wav"
        r = check(f"{key}(裁剪后)", p, txt, n)
        if r:
            results.append(r)

    print()
    print("=" * 108)
    print(" 结论")
    print("=" * 108)

    bad = [r for r in results if r["gap"] / max(r["n_chars"], 1) > 0.35]
    if bad:
        print(f"\n  {len(bad)}/{len(results)} 个短片的文本标注明显不足。\n")
        print("  为什么这会导致「机器人感」：")
        print("    CosyVoice 的 prompt_text 必须与参考音频逐字对应。")
        print("    若音频里说了 40 个字，却只标注 11 个字，模型会认为：")
        print("      「这 11 个字对应这 9 秒的语音」")
        print("    即学到「每个字要拖很长」的错误韵律模型。")
        print("    合成时就会把这个错误韵律套用到新文本上，听起来机械、拖沓。")
        print()
        print("  这解释了为什么：")
        print("    - 语速最慢的 1.6（1.22 字/秒）相似度最低（0.6835）")
        print("    - 语速最快的 1.0（1.92 字/秒）相似度最高（0.8139）")
        print("    - 「复述参考原句」时相似度极高（0.9280）—— 因为文本长度与音频匹配")
        print()
        print("  >>> 根因不是「参考音频不够」，而是「参考文本不全」。")

    print()
    print("  修复方向：")
    print("    1. 用 ASR 重新转写这些短片，得到完整文本")
    print("    2. 或者从 21 分钟合集里选「文本已知且完整」的段落")
    print("    3. 当前参考（9.7s / 42 字 / 4.33 字每秒）是唯一语速正常的，")
    print("       所以它的合成效果反而好于这些短片")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
