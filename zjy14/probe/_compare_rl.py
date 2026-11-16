"""对比 base 模型与 RL 模型，并验证文本前端的影响。

背景：模型目录同时有 llm.pt（base）和 llm.rl.pt（RL 版），
官方代码只加载 llm.pt。官方评测表显示 RL 版 speaker similarity
在 test-zh 上 77.4 vs base 78.0，差别不大，但 RL 版 RTF 更低。
真正的疑问是：换 llm.rl.pt 能否改善"不像角色"的问题？

同时验证 wetext 文本前端能否启用（当前报 no frontend is avaliable）。
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402

MODEL = "/home/user/models/Fun-CosyVoice3-0.5B-2512"
REF_WAV = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav"
REF_TXT = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt"
PREFIX = "You are a helpful assistant.<|endofprompt|>"
OUT = Path("/mnt/d/xiangmudaima/zjy14/_rl_compare")
OUT.mkdir(parents=True, exist_ok=True)

TEXT = "今天天气真不错，我们一起出去走走吧。"
SR = 24000


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
    energy = np.sqrt((frames**2).mean(axis=1))
    frames = frames[energy >= np.percentile(energy, 60)]
    if frames.shape[0] == 0:
        return np.zeros(0)
    lag_min, lag_max = int(sr / fmax), int(sr / fmin)
    out = []
    for fr in frames:
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, mode="full")[len(fr) - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lag_min:min(lag_max + 1, len(ac))]
        if seg.size == 0:
            continue
        pk = int(np.argmax(seg)) + lag_min
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


def describe(name, x, sr=SR):
    f0 = estimate_f0(x, sr)
    return {
        "name": name,
        "dur": round(len(x) / sr, 2),
        "f0": round(float(np.median(f0)), 1) if f0.size else 0.0,
        "centroid": round(centroid(x, sr), 0),
    }


def print_row(d):
    print(f"  {d['name']:<34}{d['dur']:>6.2f}s  F0{d['f0']:>7.1f}Hz  质心{d['centroid']:>7.0f}Hz")


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3

    ref_text = open(REF_TXT, encoding="utf-8").read().strip()
    if "<|endofprompt|>" not in ref_text:
        ref_text = PREFIX + ref_text

    ref, _ = sf.read(REF_WAV, dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    ref_d = describe("参考音频", ref)
    print("=" * 80)
    print(" 参考")
    print("=" * 80)
    print_row(ref_d)

    results = []

    for tag, weight in (("base", "llm.pt"), ("rl", "llm.rl.pt")):
        print()
        print("=" * 80)
        print(f" 加载 {tag} 模型 ({weight})")
        print("=" * 80)
        try:
            cosy = CosyVoice3(MODEL, fp16=False)
            # 替换 LLM 权重
            path = os.path.join(MODEL, weight)
            if tag == "rl":
                cosy.model.llm.load_state_dict(
                    torch.load(path, map_location=cosy.model.device, weights_only=True),
                    strict=True,
                )
                cosy.model.llm.to(cosy.model.device).eval()
                print(f"  已加载 {weight}")
            cosy.add_zero_shot_spk(ref_text, REF_WAV, "elysia")
            print(f"  text_frontend = {cosy.frontend.text_frontend!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  加载失败: {type(exc).__name__}: {exc}")
            continue

        def reset_hop():
            if hasattr(cosy.model, "token_hop_len"):
                cosy.model.token_hop_len = 25

        # 预热
        reset_hop()
        list(cosy.inference_zero_shot("你好。", "", REF_WAV, zero_shot_spk_id="elysia", stream=False))

        for i in range(2):
            reset_hop()
            chunks = list(cosy.inference_zero_shot(
                TEXT, "", REF_WAV, zero_shot_spk_id="elysia", stream=False))
            if not chunks:
                continue
            x = chunks[0]["tts_speech"].detach().cpu().float().numpy().squeeze()
            sf.write(str(OUT / f"{tag}_{i+1}.wav"), x, cosy.sample_rate, subtype="PCM_16")
            d = describe(f"{tag}_{i+1}", x, cosy.sample_rate)
            results.append(d)
            print_row(d)

        # 复述参考文本（最能反映克隆像不像）
        reset_hop()
        chunks = list(cosy.inference_zero_shot(
            "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀",
            "", REF_WAV, zero_shot_spk_id="elysia", stream=False))
        if chunks:
            x = chunks[0]["tts_speech"].detach().cpu().float().numpy().squeeze()
            sf.write(str(OUT / f"{tag}_复述.wav"), x, cosy.sample_rate, subtype="PCM_16")
            d = describe(f"{tag}_复述", x, cosy.sample_rate)
            results.append(d)
            print_row(d)

        # 释放
        del cosy
        torch.cuda.empty_cache()

    print()
    print("=" * 80)
    print(" 汇总（与参考的差距）")
    print("=" * 80)
    print(f"  {'样本':<34}{'F0差':>9}{'质心差':>10}")
    print("  " + "-" * 54)
    for d in results:
        print(
            f"  {d['name']:<34}{d['f0'] - ref_d['f0']:>+9.1f}"
            f"{d['centroid'] - ref_d['centroid']:>+10.0f}"
        )

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
