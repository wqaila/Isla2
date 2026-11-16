"""验证"参考音频够不够"：拼接多段参考、加长参考，看相似度能否突破。

背景（已测）：
  官方参考 vs 官方合成      相似度 0.7559   ← 这是模型上限的参考值
  爱莉希雅 复述参考原句      相似度 0.9280
  爱莉希雅 长文本(比值1.08)  相似度 0.9208
  爱莉希雅 短文本(比值0.21)  相似度 0.8568

结论矛盾点：客观相似度已超过官方基准，但用户仍觉得"不够"。
可能原因：
  H1 相似度衡量"是不是同一个人"，不衡量"说话方式/语气/韵律"
  H2 单段参考只覆盖一种语气，音色代表性不足
  H3 参考时长不够（CosyVoice 支持到 30s，我们只用 7-9.7s）

本实验测 H2/H3：
  E1 单段参考（基线）
  E2 拼接 2 段（不同语气）
  E3 拼接 3 段
  E4 加长到 20-25s（单段连续）

注意：拼接会破坏文本对应关系。CosyVoice 的 prompt_text 必须与音频逐字对应，
所以拼接时必须把文本也拼接起来（用逗号或句号分隔），否则会引入新错误。
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")

import numpy as np  # noqa: E402
import onnxruntime  # noqa: E402
import soundfile as sf  # noqa: E402
from cosyvoice.utils.file_utils import load_wav  # noqa: E402

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
CAMPPUS = MODEL / "campplus.onnx"
CAND = Path("/mnt/d/xiangmudaima/zjy14/_ref_select")
TRIM = Path("/mnt/d/xiangmudaima/zjy14/_trim_test")
SRC = Path("/mnt/c/Users/user/Desktop/aili")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_ref_concat")
OUT.mkdir(parents=True, exist_ok=True)
SR = 24000
PREFIX = "You are a helpful assistant.<|endofprompt|>"

TEXTS = {
    "1.0": "早上好，新的一天从一场美妙的邂逅开始",
    "1.3": "快去休息了，约好了我们在梦里相见",
    "1.4": "这么晚了还不睡吗，是在想我对不对",
    "1.5": "睡不着的话就来聊聊天吧",
    "1.6": "晚上好，如此美妙的夜晚",
    "1.7": "月亮升起来了，要陪我一起去看星星吗",
}

# 合成文本：长文本保证比值达标
SYNTH = (
    "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨，"
    "窗外的树叶被打得七零八落，今天总算放晴了。"
)


# ------------------------------------------------------------ 相似度


def build_session() -> onnxruntime.InferenceSession:
    opt = onnxruntime.SessionOptions()
    opt.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
    opt.intra_op_num_threads = 1
    return onnxruntime.InferenceSession(
        str(CAMPPUS), sess_options=opt, providers=["CPUExecutionProvider"]
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


def sim(a, b) -> float:
    return float(np.dot(a, b))


# ------------------------------------------------------------ 音频处理


def trim_silence(x, sr, rel_thr=0.02, pad_s=0.05):
    hop = int(sr * 0.01)
    n = len(x) // hop
    if n == 0:
        return x
    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return x
    voiced = rms > max(peak * rel_thr, 1e-6)
    idx = np.where(voiced)[0]
    if idx.size == 0:
        return x
    a = max(0, int(idx[0] * hop - pad_s * sr))
    b = min(len(x), int((idx[-1] + 1) * hop + pad_s * sr))
    return x[a:b]


def load_clean(key: str) -> tuple[np.ndarray, str] | None:
    """加载候选并裁静音。"""
    p = CAND / f"cand_{key}.wav"
    if not p.is_file():
        return None
    x, sr = sf.read(str(p), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return trim_silence(x, sr), TEXTS.get(key, "")


def concat_refs(parts: list[tuple[np.ndarray, str]], gap_s: float = 0.12):
    """拼接多段参考，中间留短静音间隔。文本用句号连接。"""
    gap = np.zeros(int(gap_s * SR), dtype=np.float32)
    chunks = []
    for i, (audio, _) in enumerate(parts):
        if i > 0:
            chunks.append(gap)
        chunks.append(audio.astype(np.float32))
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    text = "。".join(p[1] for p in parts if p[1]) + "。"
    return audio, text


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3

    sess = build_session()

    print("=" * 104)
    print(" 参考音频组合实验：能否突破当前相似度")
    print("=" * 104)

    # ---- 准备各种参考 ----
    variants: list[tuple[str, Path, str]] = []

    # E1 单段（基线，用之前表现最好的）
    for key in ("1.5", "1.0", "1.6"):
        got = load_clean(key)
        if not got:
            continue
        audio, text = got
        p = OUT / f"E1_single_{key}.wav"
        sf.write(str(p), audio, SR, subtype="PCM_16")
        variants.append((f"E1_单段{key}({len(audio)/SR:.1f}s)", p, text))

    # E2 拼接 2 段
    combos2 = [("1.5", "1.6"), ("1.0", "1.5"), ("1.4", "1.6")]
    for a, b in combos2:
        ga, gb = load_clean(a), load_clean(b)
        if not ga or not gb:
            continue
        audio, text = concat_refs([ga, gb])
        p = OUT / f"E2_{a}+{b}.wav"
        sf.write(str(p), audio, SR, subtype="PCM_16")
        variants.append((f"E2_{a}+{b}({len(audio)/SR:.1f}s)", p, text))

    # E3 拼接 3 段
    trio = ("1.0", "1.5", "1.6")
    gs = [load_clean(k) for k in trio]
    if all(gs):
        audio, text = concat_refs(gs)  # type: ignore[arg-type]
        p = OUT / "E3_3段.wav"
        sf.write(str(p), audio, SR, subtype="PCM_16")
        variants.append((f"E3_3段({len(audio)/SR:.1f}s)", p, text))

    # E4 加长：从合集取 20-25s 连续段（文本未知，跳过——避免文本错位）
    print("\n  [提示] E4（加长到 20s+）需要对应文本，"
          "合集段落文本未知，跳过以免引入文本错位错误。")

    print()
    print(f"  {'参考':<28}{'时长':>7}{'字符':>7}")
    print("  " + "-" * 44)
    for label, p, text in variants:
        info = sf.info(str(p))
        print(f"  {label:<28}{info.duration:>6.1f}s{len(text):>7}")

    # ---- 合成 ----
    print()
    print("=" * 104)
    print(" 合成 + 相似度")
    print("=" * 104)

    cosy = CosyVoice3(MODEL, fp16=False)
    print(f"  text_frontend = {cosy.frontend.text_frontend!r}")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    rows = []
    for label, wav_path, txt in variants:
        prompt = txt if "<|endofprompt|>" in txt else PREFIX + txt
        ratio = len(SYNTH) / len(prompt)
        reset_hop()
        try:
            chunks = list(cosy.inference_zero_shot(
                SYNTH, prompt, str(wav_path), zero_shot_spk_id="", stream=False))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{label}] 失败: {type(exc).__name__}: {exc}")
            continue
        if not chunks:
            continue
        parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze() for c in chunks]
        y = np.concatenate(parts) if len(parts) > 1 else parts[0]
        out_wav = OUT / f"synth_{label.split('(')[0]}.wav"
        sf.write(str(out_wav), y, SR, subtype="PCM_16")

        r_emb = embed(sess, wav_path)
        s_emb = embed(sess, out_wav)
        s = sim(r_emb, s_emb)

        # 也与"当前参考"比（衡量是否更像原始选段）
        rows.append((label, s, ratio, len(y) / SR, len(chunks)))
        print(f"  {label:<28}相似度 {s:.4f}  比值 {ratio:.2f}  "
              f"音频 {len(y)/SR:.1f}s  块 {len(chunks)}")

    print()
    print("=" * 104)
    print(" 汇总（按相似度排序）")
    print("=" * 104)
    print(f"  {'参考':<28}{'相似度':>9}{'文本比值':>10}{'音频时长':>10}   判断")
    print("  " + "-" * 76)
    for label, s, ratio, dur, nch in sorted(rows, key=lambda r: -r[1]):
        v = "同一人" if s >= 0.85 else ("很像" if s >= 0.70 else "有相似性")
        print(f"  {label:<28}{s:>9.4f}{ratio:>10.2f}{dur:>9.1f}s   {v}")

    # ---- 参考之间的一致性 ----
    print()
    print("=" * 104)
    print(" 参考组合之间的一致性（判断拼接是否引入了不同音色）")
    print("=" * 104)
    embs = {}
    for label, p, _ in variants:
        embs[label] = embed(sess, p)
    names = list(embs)
    print(f"  {'':<28}" + "".join(f"{i+1:>9}" for i in range(len(names))))
    print("  " + "-" * (28 + 9 * len(names)))
    for i, a in enumerate(names):
        row = f"  {i+1}. {a:<25}"
        for b in names:
            row += f"{sim(embs[a], embs[b]):>9.3f}"
        print(row)

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
