"""决定性验证：文本长度比是否是"像机器人"的根因。

发现：inference_zero_shot 有个官方警告
  cosyvoice.py:94-95
  if len(i) < 0.5 * len(prompt_text):
      logging.warning('synthesis text too short than prompt text, this may lead to bad performance')

当前情况：
  参考文本 85 字符（9.7s 音频）
  合成文本 18 字符（"今天天气真不错，我们一起出去走走吧。"）
  比值 0.21 << 0.5  → 必然触发警告

假设：参考音频过长 + 合成文本过短，导致模型在生成短句时
     被参考的长韵律"带跑"，音色与韵律都失真。

验证矩阵：
  A. 短参考(3s) + 短文本  → 比值达标，预期最好
  B. 长参考(9.7s) + 长文本 → 比值达标，验证长文本是否正常
  C. 长参考(9.7s) + 短文本 → 当前情况，预期最差
  D. 中等参考(5s) + 短文本 → 中间态

评价：合成音频与参考音频的 HNR/质心/平坦度差异，以及绝对质量。
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
REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_ratio_test")
OUT.mkdir(parents=True, exist_ok=True)
SR = 24000
PREFIX = "You are a helpful assistant.<|endofprompt|>"

# 参考文本（9.7s 音频对应的完整文本）
REF_TEXT = "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀"

# 短文本 / 长文本
SHORT_TEXT = "今天天气真不错，我们一起出去走走吧。"
LONG_TEXT = (
    "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨，"
    "窗外的树叶被打得七零八落，今天总算放晴了，空气里都是青草的味道。"
    "我想去公园那边的湖边看看，听说荷花开了一片，顺便可以买杯奶茶。"
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


def f0med(x, sr, fmin=70.0, fmax=450.0):
    fr = frames(x, sr)
    if fr.shape[0] == 0:
        return 0.0
    e = np.sqrt((fr**2).mean(axis=1))
    fr = fr[e >= np.percentile(e, 70)]
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


def describe(name, x, sr=SR):
    return {
        "name": name,
        "dur": round(len(x) / sr, 2),
        "f0": round(f0med(x, sr), 1),
        "flat": round(flatness(x, sr), 4),
        "hnr": round(hnr(x, sr), 1),
        "centroid": round(centroid(x, sr), 0),
    }


def print_row(d):
    print(
        f"  {d['name']:<34}{d['dur']:>6.2f}s  F0{d['f0']:>7.1f}Hz  "
        f"平坦{d['flat']:>7.4f}  HNR{d['hnr']:>6.1f}dB  质心{d['centroid']:>6.0f}Hz"
    )


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3

    ref, _ = sf.read(str(REF), dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    print("=" * 116)
    print(" 参考音频")
    print("=" * 116)
    print_row(describe("完整参考(9.7s)", ref))

    # 切出不同长度（对应文本按比例截取）
    # 注意：截断音频但文本必须对应，否则会引入新错误。
    # 这里用"只截音频不截文本"是错的，所以改为：不切，而是换用不同长度的合成文本。
    # 另外准备一份 3s 参考：用 1.5.mp3（9.59s，文本 54 字符）
    cand = Path("/mnt/d/xiangmudaima/zjy14/_ref_select/cand_1.5.wav")
    cand_text = "睡不着的话就来聊聊天吧"
    if cand.is_file():
        c, _ = sf.read(str(cand), dtype="float32")
        if c.ndim > 1:
            c = c.mean(axis=1)
        print_row(describe("候选1.5(9.6s)", c))

    print()
    print("=" * 116)
    print(" 文本长度比验证（核心）")
    print("=" * 116)

    cosy = CosyVoice3(MODEL, fp16=False)
    print(f"  text_frontend = {cosy.frontend.text_frontend!r}")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    prompt = PREFIX + REF_TEXT
    print(f"  参考文本 {len(prompt)} 字符")
    print(f"  短文本 {len(SHORT_TEXT)} 字符 -> 比值 {len(SHORT_TEXT)/len(prompt):.2f}")
    print(f"  长文本 {len(LONG_TEXT)} 字符 -> 比值 {len(LONG_TEXT)/len(prompt):.2f}")
    print()

    results = []

    def run(label, text):
        reset_hop()
        try:
            chunks = list(cosy.inference_zero_shot(
                text, prompt, str(REF), zero_shot_spk_id="", stream=False))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{label}] 失败: {type(exc).__name__}: {exc}")
            return
        if not chunks:
            print(f"  [{label}] 无输出")
            return
        # 拼接所有 chunk
        parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze() for c in chunks]
        y = np.concatenate(parts) if len(parts) > 1 else parts[0]
        sf.write(str(OUT / f"{label}.wav"), y, SR, subtype="PCM_16")
        d = describe(label, y)
        results.append(d)
        ratio = len(text) / len(prompt)
        print_row(d)
        print(f"      文本{len(text)}字符 比值{ratio:.2f} 块数{len(chunks)}")

    # 预热
    reset_hop()
    list(cosy.inference_zero_shot("你好。", prompt, str(REF), stream=False))

    print("  --- 短文本（当前问题场景）---")
    run("C_长参考+短文本", SHORT_TEXT)

    print("  --- 长文本（比值达标）---")
    run("B_长参考+长文本", LONG_TEXT)

    print()
    print("  --- 用短参考(1.5) + 短文本 ---")
    if cand.is_file():
        cand_prompt = PREFIX + cand_text
        # 临时替换参考
        def run_cand(label, text, wav, prm):
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
            print(f"      文本{len(text)}字符 比值{len(text)/len(prm):.2f} 块数{len(chunks)}")

        run_cand("A_短参考+短文本", SHORT_TEXT, cand, cand_prompt)
        run_cand("A2_短参考+长文本", LONG_TEXT, cand, cand_prompt)

    print()
    print("=" * 116)
    print(" 汇总")
    print("=" * 116)
    print(f"  {'样本':<34}{'时长':>7}{'F0':>9}{'平坦度':>9}{'HNR':>9}{'质心':>9}")
    print("  " + "-" * 78)
    for d in results:
        print(
            f"  {d['name']:<34}{d['dur']:>6.2f}s{d['f0']:>9.1f}"
            f"{d['flat']:>9.4f}{d['hnr']:>8.1f}dB{d['centroid']:>8.0f}Hz"
        )

    print()
    print("  参考基准:")
    print_row(describe("参考(9.7s)", ref))

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
