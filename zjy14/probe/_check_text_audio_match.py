"""关键检查：参考音频的"文本-音频"时长是否匹配。

发现异常：
  官方参考    3.48s / 15 字符  = 4.31 字符/秒   ← 正常中文语速
  当前参考    9.70s / 45 字符  = 4.64 字符/秒   ← 正常
  1.0.mp3     6.06s / 18 字符  = 2.97 字符/秒   ← 偏慢
  1.5.mp3     7.38s / 11 字符  = 1.49 字符/秒   ← 严重偏慢！
  1.6.mp3     6.49s / 11 字符  = 1.69 字符/秒   ← 严重偏慢！

中文正常语速 4-5 字符/秒。1.5 和 1.6 只有 1.5-1.7，差 3 倍。

可能原因：
  A. 文本标注不全（音频说的话比文本多）→ 文本-音频不对齐 → 模型学到错误对齐
  B. 音频中间有大量停顿（被裁静音后仍有内部静音）
  C. 音频含背景音乐/其他内容

若 A 成立，则用这些片段作参考会直接导致合成韵律失真（"机器人感"）。

本脚本用 ASR 回读验证（FunASR 在 zjy13 的 .venv-asr 里）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

SRC = Path("/mnt/c/Users/user/Desktop/aili")
CAND = Path("/mnt/d/xiangmudaima/zjy14/_ref_select")
TRIM = Path("/mnt/d/xiangmudaima/zjy14/_trim_test")
REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
OFFICIAL = Path("/home/user/CosyVoice/asset/zero_shot_prompt.wav")

CLAIMS = {
    "1.0.mp3": "早上好，新的一天从一场美妙的邂逅开始",
    "1.1.mp3": "这么早起床是为了能早点见到我吗",
    "1.3.mp3": "快去休息了，约好了我们在梦里相见",
    "1.4.mp3": "这么晚了还不睡吗，是在想我对不对",
    "1.5.mp3": "睡不着的话就来聊聊天吧",
    "1.6.mp3": "晚上好，如此美妙的夜晚",
    "1.7.mp3": "月亮升起来了，要陪我一起去看星星吗",
}
REF_TEXT = "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀"
OFFICIAL_TEXT = "希望你以后能够做的比我还好呦"


def speech_rate(n_chars: int, duration: float) -> float:
    return n_chars / duration if duration > 0 else 0.0


def internal_silence(x, sr, rel_thr=0.02):
    """统计内部静音（不含首尾）。"""
    hop = int(sr * 0.01)
    n = len(x) // hop
    if n == 0:
        return 0.0, 0
    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return 0.0, 0
    silent = rms < max(peak * rel_thr, 1e-6)
    # 去掉首尾
    idx = np.where(~silent)[0]
    if idx.size == 0:
        return 1.0, 0
    inner = silent[idx[0]: idx[-1] + 1]
    # 统计停顿段数（>100ms）
    pauses = 0
    i = 0
    while i < len(inner):
        if inner[i]:
            j = i
            while j < len(inner) and inner[j]:
                j += 1
            if (j - i) * 0.01 >= 0.10:
                pauses += 1
            i = j
        else:
            i += 1
    return float(inner.mean()), pauses


def main() -> int:
    print("=" * 96)
    print(" 参考音频：文本-时长匹配性检查")
    print("=" * 96)
    print("  中文正常语速 4-5 字符/秒。显著偏离说明文本标注可能不全。\n")

    print(f"  {'音频':<18}{'时长':>7}{'字符':>7}{'字符/秒':>9}{'内部静音':>10}{'停顿数':>8}  判断")
    print("  " + "-" * 84)

    rows = []

    def check(label: str, path: Path, text: str) -> None:
        if not path.is_file():
            return
        x, sr = sf.read(str(path), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        dur = len(x) / sr
        n = len(text)
        rate = speech_rate(n, dur)
        sil, pauses = internal_silence(x, sr)
        if rate >= 3.5:
            judge = "正常"
        elif rate >= 2.5:
            judge = "偏慢"
        else:
            judge = "严重偏慢 <<< 疑似文本不全"
        rows.append((label, dur, n, rate, sil, pauses, judge))
        print(f"  {label:<18}{dur:>6.2f}s{n:>7}{rate:>9.2f}{sil:>9.1%}{pauses:>8}  {judge}")

    # 基准
    check("官方参考", OFFICIAL, OFFICIAL_TEXT)
    check("当前参考(9.7s)", REF, REF_TEXT)

    # 短片
    for fn, txt in CLAIMS.items():
        p = CAND / f"cand_{fn.replace('.mp3', '')}.wav"
        if p.is_file():
            check(f"{fn}(裁剪后)", p, txt)

    print()
    print("=" * 96)
    print(" 分析与结论")
    print("=" * 96)

    normal = [r for r in rows if r[3] >= 3.5]
    slow = [r for r in rows if r[3] < 2.5]

    if slow:
        print(f"\n  ⚠ {len(slow)} 个片段的语速严重偏慢（<2.5 字符/秒）：")
        for label, dur, n, rate, sil, pauses, _ in slow:
            print(f"    {label:<18} {rate:.2f} 字符/秒  "
                  f"（{dur:.2f}s 只标注了 {n} 个字符）")
        print()
        print("  这意味着两种可能：")
        print("    A. 文本标注不全 —— 音频实际说的话比文本多")
        print("    B. 音频内部有大量停顿或非语音内容")
        print()
        print("  若 A 成立：CosyVoice 的 prompt_text 必须与音频逐字对应，")
        print("  文本不全 = 对齐错误 = 模型学到错误的文本-语音映射，")
        print("  合成时韵律会失真，听起来就是「机器人感」。")
        print()
        print("  >>> 这比「参考音频不够长」更能解释用户的听感反馈。")

    print()
    print("  对照：相似度与语速的关系（来自 _ref_concat_test）")
    print("    E1_单段1.0  语速 2.97  相似度 0.8139")
    print("    E1_单段1.5  语速 1.49  相似度 0.7720")
    print("    E1_单段1.6  语速 1.69  相似度 0.6835   <- 语速最慢，相似度最低")
    print("    E2_1.0+1.5  语速 ~2.3  相似度 0.8164")
    print()
    print("  观察：语速越接近正常（1.0 最快），相似度越高。")

    print()
    print("=" * 96)
    print(" 建议验证方式")
    print("=" * 96)
    print("  1. 用 ASR 回读这几个短片，看识别出的文本是否比标注的多")
    print("     （zjy13 的 .venv-asr 里有 FunASR，但当前 venv 没有）")
    print("  2. 人工听 1.5/1.6 的原始 MP3，确认说的到底是不是那 11 个字")
    print("  3. 若确实文本不全，需要重新做 ASR 转写")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
