"""验证文本前端启用效果：韵律指标是否改善。

对比：
  OFF  未启用 text_frontend（当前状态）
  ON   启用 wetext 本地前端

用同一参考、同一文本，看音节CV、F0起伏等韵律指标变化。
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")
sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

import numpy as np  # noqa: E402
import onnxruntime  # noqa: E402
import soundfile as sf  # noqa: E402
from cosyvoice.utils.file_utils import load_wav  # noqa: E402

from custom_voice.inference.text_frontend import (  # noqa: E402
    enable_wetext_frontend,
    frontend_status,
)

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
CAMPPUS = MODEL / "campplus.onnx"
REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_tn_test")
OUT.mkdir(parents=True, exist_ok=True)
SR = 24000
PREFIX = "You are a helpful assistant.<|endofprompt|>"

# 含数字/符号的文本，最能体现 TN 价值
TEXT_WITH_NUMBERS = "现在是2026年11月9日，温度25度，我买了3.5斤苹果，花了100块钱。"
# 纯中文文本（TN 影响较小，作为对照）
TEXT_PLAIN = "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。"


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


def syllable_cv(x, sr):
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
    cv, n = syllable_cv(x, sr)
    return {
        "name": name,
        "dur": round(len(x) / sr, 2),
        "f0": round(float(np.median(f0)), 1) if f0.size else 0.0,
        "f0cv": round(float(np.std(f0) / max(np.mean(f0), 1e-9)), 3) if f0.size else 0.0,
        "syl_cv": round(cv, 3),
        "syl_n": n,
    }


def print_row(d):
    print(
        f"  {d['name']:<26}{d['dur']:>6.2f}s  F0{d['f0']:>7.1f}Hz  "
        f"F0起伏{d['f0cv']:>5.3f}  音节CV{d['syl_cv']:>5.3f}({d['syl_n']:>3})"
    )


def embed(session, wav_path: Path) -> np.ndarray:
    import torchaudio.compliance.kaldi as kaldi

    speech = load_wav(str(wav_path), 16000)
    feat = kaldi.fbank(speech, num_mel_bins=80, dither=0, sample_frequency=16000)
    feat = feat - feat.mean(dim=0, keepdim=True)
    out = session.run(
        None, {session.get_inputs()[0].name: feat.unsqueeze(dim=0).cpu().numpy()}
    )[0].flatten()
    v = np.asarray(out, dtype=np.float64)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3

    print("=" * 108)
    print(" 文本前端（wetext）启用效果验证")
    print("=" * 108)

    opt = onnxruntime.SessionOptions()
    opt.intra_op_num_threads = 1
    sess = onnxruntime.InferenceSession(
        str(CAMPPUS), sess_options=opt, providers=["CPUExecutionProvider"]
    )
    ref_emb = embed(sess, REF)

    ref_text = (
        "You are a helpful assistant.<|endofprompt|>"
        "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡"
        "这可是身为副首领最重要的职责呀"
    )

    results = []

    for tag, enable in (("OFF_无前端", False), ("ON_wetext", True)):
        print()
        print("-" * 108)
        print(f" {tag}")
        print("-" * 108)

        cosy = CosyVoice3(MODEL, fp16=False)
        if enable:
            try:
                ok = enable_wetext_frontend(cosy.frontend)
                print(f"  启用结果: {ok}")
            except Exception as exc:  # noqa: BLE001
                print(f"  启用失败: {type(exc).__name__}: {exc}")
                continue
        st = frontend_status(cosy.frontend)
        print(f"  frontend: {st['text_frontend']!r}  "
              f"zh={st['has_zh_model']} en={st['has_en_model']}")

        cosy.add_zero_shot_spk(ref_text, str(REF), "elysia")

        def reset_hop():
            if hasattr(cosy.model, "token_hop_len"):
                cosy.model.token_hop_len = 25

        # 预热
        reset_hop()
        list(cosy.inference_zero_shot("你好。", "", str(REF), zero_shot_spk_id="elysia", stream=False))

        for label, text in (("含数字", TEXT_WITH_NUMBERS), ("纯中文", TEXT_PLAIN)):
            reset_hop()
            try:
                chunks = list(cosy.inference_zero_shot(
                    text, "", str(REF), zero_shot_spk_id="elysia", stream=False))
            except Exception as exc:  # noqa: BLE001
                print(f"  [{label}] 失败: {type(exc).__name__}: {exc}")
                continue
            if not chunks:
                continue
            parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                     for c in chunks]
            y = np.concatenate(parts) if len(parts) > 1 else parts[0]
            wav = OUT / f"{tag}_{label}.wav"
            sf.write(str(wav), y, SR, subtype="PCM_16")
            d = describe(f"{tag}_{label}", y, SR)
            d["sim"] = round(float(np.dot(ref_emb, embed(sess, wav))), 4)
            results.append(d)
            print_row(d)
            print(f"      与参考相似度 {d['sim']:.4f}")

        del cosy
        import torch
        torch.cuda.empty_cache()

    print()
    print("=" * 108)
    print(" 汇总对比")
    print("=" * 108)
    print(f"  {'样本':<26}{'时长':>7}{'F0':>9}{'F0起伏':>9}{'音节CV':>9}{'相似度':>9}")
    print("  " + "-" * 72)
    for d in results:
        print(
            f"  {d['name']:<26}{d['dur']:>6.2f}s{d['f0']:>9.1f}"
            f"{d['f0cv']:>9.3f}{d['syl_cv']:>9.3f}{d['sim']:>9.4f}"
        )

    print()
    print("  参考基准：")
    ref, _ = sf.read(str(REF), dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    print_row(describe("当前参考", ref))

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
