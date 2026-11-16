"""严谨对比 TN 效果：多次采样取统计量，排除随机性干扰。

背景（已实测）：
  同一文本重复 4 次合成：
    时长    5.24-7.64s   标准差 0.97s
    音节CV  0.487-0.869  标准差 0.160

  模型采样参数 top_p=0.8 / top_k=25，随机性很大。
  之前用单次合成对比 ON/OFF 的结论不可靠。

本测试：
  每组 5 次采样，报告均值/中位数/标准差，
  用中位数对比（对异常值更稳健）。
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

from custom_voice.inference.text_frontend import enable_wetext_frontend  # noqa: E402

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
CAMPPUS = MODEL / "campplus.onnx"
REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_tn_stats")
OUT.mkdir(parents=True, exist_ok=True)
SR = 24000
N_SAMPLES = 5

REF_TEXT = (
    "You are a helpful assistant.<|endofprompt|>"
    "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡"
    "这可是身为副首领最重要的职责呀"
)

# 含数字的文本（TN 价值最明显）
TEXT = "现在是2026年11月9日，温度25度，我买了3.5斤苹果，花了100块钱。"


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
    vi = np.where(voiced)[0]
    if vi.size == 0:
        return 0.0, 0
    seg = voiced[vi[0]: vi[-1] + 1]
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


def stats(vals: list[float]) -> tuple[float, float, float]:
    a = np.array(vals)
    return float(a.mean()), float(np.median(a)), float(a.std())


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3

    opt = onnxruntime.SessionOptions()
    opt.intra_op_num_threads = 1
    sess = onnxruntime.InferenceSession(
        str(CAMPPUS), sess_options=opt, providers=["CPUExecutionProvider"]
    )
    ref_emb = embed(sess, REF)

    print("=" * 104)
    print(f" TN 效果严谨对比（每组 {N_SAMPLES} 次采样）")
    print("=" * 104)
    print(f"  合成文本: {TEXT}")
    print()

    groups = {}

    for tag, enable in (("OFF_无前端", False), ("ON_wetext", True)):
        print("-" * 104)
        print(f" {tag}")
        print("-" * 104)

        cosy = CosyVoice3(MODEL, fp16=False)
        if enable:
            enable_wetext_frontend(cosy.frontend)
        print(f"  text_frontend = {cosy.frontend.text_frontend!r}")
        cosy.add_zero_shot_spk(REF_TEXT, str(REF), "elysia")

        def reset_hop():
            if hasattr(cosy.model, "token_hop_len"):
                cosy.model.token_hop_len = 25

        reset_hop()
        list(cosy.inference_zero_shot("你好。", "", str(REF), zero_shot_spk_id="elysia", stream=False))

        durs, cvs, sims, f0s = [], [], [], []
        for i in range(N_SAMPLES):
            reset_hop()
            try:
                chunks = list(cosy.inference_zero_shot(
                    TEXT, "", str(REF), zero_shot_spk_id="elysia", stream=False))
            except Exception as exc:  # noqa: BLE001
                print(f"    第{i+1}次失败: {type(exc).__name__}: {exc}")
                continue
            if not chunks:
                continue
            parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                     for c in chunks]
            y = np.concatenate(parts) if len(parts) > 1 else parts[0]
            wav = OUT / f"{tag}_{i+1}.wav"
            sf.write(str(wav), y, SR, subtype="PCM_16")

            cv, n = syllable_cv(y, SR)
            f0 = f0_series(y, SR)
            s = float(np.dot(ref_emb, embed(sess, wav)))
            dur = len(y) / SR
            durs.append(dur)
            cvs.append(cv)
            sims.append(s)
            if f0.size:
                f0s.append(float(np.median(f0)))
            print(f"    第{i+1}次  时长 {dur:5.2f}s  音节CV {cv:.3f}({n:>2})  "
                  f"F0 {np.median(f0) if f0.size else 0:6.1f}Hz  相似度 {s:.4f}")

        if durs:
            groups[tag] = {
                "dur": durs, "cv": cvs, "sim": sims, "f0": f0s,
            }
        del cosy
        import torch
        torch.cuda.empty_cache()
        print()

    print("=" * 104)
    print(" 统计对比（中位数更稳健）")
    print("=" * 104)
    print(f"  {'指标':<12}{'组':<12}{'均值':>10}{'中位数':>10}{'标准差':>10}")
    print("  " + "-" * 56)

    for metric, label, fmt in (
        ("dur", "时长(s)", "{:.2f}"),
        ("cv", "音节CV", "{:.3f}"),
        ("sim", "相似度", "{:.4f}"),
        ("f0", "F0(Hz)", "{:.1f}"),
    ):
        for tag in ("OFF_无前端", "ON_wetext"):
            if tag not in groups or not groups[tag][metric]:
                continue
            m, med, sd = stats(groups[tag][metric])
            print(f"  {label:<12}{tag:<12}{fmt.format(m):>10}"
                  f"{fmt.format(med):>10}{fmt.format(sd):>10}")
        print()

    print("=" * 104)
    print(" 结论")
    print("=" * 104)

    if "OFF_无前端" in groups and "ON_wetext" in groups:
        for metric, label in (("dur", "时长"), ("cv", "音节CV"), ("sim", "相似度")):
            off = groups["OFF_无前端"].get(metric, [])
            on = groups["ON_wetext"].get(metric, [])
            if not off or not on:
                continue
            _, med_off, sd_off = stats(off)
            _, med_on, sd_on = stats(on)
            diff = med_on - med_off
            # 判断差异是否超过噪声
            pooled_sd = (sd_off + sd_on) / 2
            significant = abs(diff) > max(pooled_sd, 1e-9)
            print(f"  {label}: OFF 中位 {med_off:.4f} -> ON 中位 {med_on:.4f}  "
                  f"差 {diff:+.4f}  "
                  f"{'显著' if significant else '不显著（在噪声范围内）'}")

    print()
    print("  说明：TN 的核心价值是「数字被正确朗读」，")
    print("  这一点已通过文本转换直接验证（2026 -> 二零二六）。")
    print("  韵律指标因采样随机性大，需大样本才能定论。")

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
