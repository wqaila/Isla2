"""判定问题在"模型/环境"还是"爱莉希雅参考素材"。

对照组设计：
  Control  官方 asset/zero_shot_prompt.wav + 官方文本 → 若正常，说明模型与环境没问题
  Elysia   现有 ref_elysia.wav (9.7s)               → 用户反馈"像机器人"
  Elysia-5s / Elysia-3s                             → 缩短参考，验证是否过长导致泛化差
  Elysia-other                                      → 从同一素材另取一段，验证是否选段问题

同时检查参考音频是否含音乐/混响（谐波结构分析）。
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
COSY = Path("/home/user/CosyVoice")
REF_WAV = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
REF_TXT = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt")
PREFIX = "You are a helpful assistant.<|endofprompt|>"
OUT = Path("/mnt/d/xiangmudaima/zjy14/_ref_ab")
OUT.mkdir(parents=True, exist_ok=True)

TEXT = "今天天气真不错，我们一起出去走走吧。"
SR = 24000


# ------------------------------------------------------------ 特征


def frame_signal(x, sr, win_s=0.04, hop_s=0.02):
    win, hop = int(sr * win_s), int(sr * hop_s)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return np.zeros((0, win))
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return x[idx].astype(np.float64)


def estimate_f0(x, sr, fmin=60.0, fmax=500.0):
    frames = frame_signal(x, sr)
    if frames.shape[0] == 0:
        return np.zeros(0)
    e = np.sqrt((frames**2).mean(axis=1))
    frames = frames[e >= np.percentile(e, 60)]
    if frames.shape[0] == 0:
        return np.zeros(0)
    lo, hi = int(sr / fmax), int(sr / fmin)
    out = []
    for fr in frames:
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, mode="full")[len(fr) - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:min(hi + 1, len(ac))]
        if seg.size == 0:
            continue
        pk = int(np.argmax(seg)) + lo
        if ac[pk] < 0.3 * ac[0]:
            continue
        out.append(sr / pk)
    return np.array(out)


def centroid(x, sr):
    frames = frame_signal(x, sr)
    if frames.shape[0] == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(frames * np.hanning(frames.shape[1]), axis=1))
    freqs = np.fft.rfftfreq(frames.shape[1], 1.0 / sr)
    mag = spec.sum(axis=0)
    return float((mag * freqs).sum() / mag.sum()) if mag.sum() > 0 else 0.0


def flatness(x, sr):
    """频谱平坦度。接近 1 = 噪声/嘶声；接近 0 = 强谐波（乐音/浊音）。"""
    frames = frame_signal(x, sr)
    if frames.shape[0] == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(frames * np.hanning(frames.shape[1]), axis=1)) + 1e-12
    gm = np.exp(np.mean(np.log(spec), axis=1))
    am = np.mean(spec, axis=1)
    return float(np.median(gm / am))


def f0_stability(x, sr):
    """F0 相对标准差。越小越"平"，越大越有起伏。"""
    f0 = estimate_f0(x, sr)
    if f0.size < 2:
        return 0.0
    return float(np.std(f0) / max(np.mean(f0), 1e-9))


def describe(name, x, sr=SR):
    f0 = estimate_f0(x, sr)
    return {
        "name": name,
        "dur": round(len(x) / sr, 2),
        "f0": round(float(np.median(f0)), 1) if f0.size else 0.0,
        "centroid": round(centroid(x, sr), 0),
        "flat": round(flatness(x, sr), 4),
        "f0cv": round(f0_stability(x, sr), 3),
    }


def print_row(d):
    print(
        f"  {d['name']:<32}{d['dur']:>6.2f}s  F0{d['f0']:>7.1f}Hz"
        f"  质心{d['centroid']:>6.0f}Hz  平坦度{d['flat']:>7.4f}  F0起伏{d['f0cv']:>6.3f}"
    )


def slice_audio(x, sr, seconds):
    n = int(seconds * sr)
    return x[:n] if len(x) > n else x


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3

    ref_text = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" not in ref_text:
        ref_text = PREFIX + ref_text

    elysia, _ = sf.read(str(REF_WAV), dtype="float32")
    if elysia.ndim > 1:
        elysia = elysia.mean(axis=1)

    official_wav = COSY / "asset" / "zero_shot_prompt.wav"
    official_text = PREFIX + "希望你以后能够做的比我还好呦。"

    print("=" * 100)
    print(" 参考音频特征对比（判断素材本身是否有问题）")
    print("=" * 100)
    refs = {}
    refs["官方示例"] = (official_wav, official_text, None)

    off_x, off_sr = sf.read(str(official_wav), dtype="float32")
    if off_x.ndim > 1:
        off_x = off_x.mean(axis=1)
    d = describe("官方示例参考", off_x, off_sr)
    print_row(d)

    d = describe("爱莉希雅参考(9.7s)", elysia)
    print_row(d)

    # 缩短版本
    for secs in (5.0, 3.0):
        seg = slice_audio(elysia, SR, secs)
        p = OUT / f"elysia_{int(secs)}s.wav"
        sf.write(str(p), seg, SR, subtype="PCM_16")
        d = describe(f"爱莉希雅({int(secs)}s)", seg)
        print_row(d)
        refs[f"爱莉希雅{int(secs)}s"] = (p, ref_text, None)

    # 从后段另取一段（同一文件后半部分）
    mid = len(elysia) // 2
    other = elysia[mid:]
    if len(other) > SR * 3:
        p = OUT / "elysia_other.wav"
        sf.write(str(p), other, SR, subtype="PCM_16")
        d = describe("爱莉希雅(后半段)", other)
        print_row(d)

    print()
    print("=" * 100)
    print(" 合成对比")
    print("=" * 100)

    cosy = CosyVoice3(MODEL, fp16=False)
    print(f"  text_frontend = {cosy.frontend.text_frontend!r}")
    results = []

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    def run(label, wav_path, prompt_text):
        reset_hop()
        try:
            chunks = list(cosy.inference_zero_shot(
                TEXT, prompt_text, str(wav_path), zero_shot_spk_id="", stream=False))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{label}] 失败: {type(exc).__name__}: {exc}")
            return
        if not chunks:
            print(f"  [{label}] 无输出")
            return
        x = chunks[0]["tts_speech"].detach().cpu().float().numpy().squeeze()
        sf.write(str(OUT / f"{label}.wav"), x, SR, subtype="PCM_16")
        d = describe(label, x)
        results.append(d)
        print_row(d)

    # 预热（用官方音频）
    reset_hop()
    list(cosy.inference_zero_shot("你好。", official_text, str(official_wav), stream=False))

    # 对照组
    for i in range(2):
        run(f"官方{i+1}", official_wav, official_text)

    # 实验组
    for i in range(2):
        run(f"爱莉9.7s_{i+1}", REF_WAV, ref_text)

    for secs in (5, 3):
        run(f"爱莉{secs}s", OUT / f"elysia_{secs}s.wav", ref_text)

    print()
    print("=" * 100)
    print(" 汇总")
    print("=" * 100)
    print(f"  {'样本':<32}{'F0':>9}{'质心':>9}{'平坦度':>9}{'F0起伏':>9}")
    print("  " + "-" * 68)
    for d in results:
        print(
            f"  {d['name']:<32}{d['f0']:>9.1f}{d['centroid']:>9.0f}"
            f"{d['flat']:>9.4f}{d['f0cv']:>9.3f}"
        )

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
