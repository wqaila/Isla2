"""客观量化"像不像角色"：用模型自带的 campplus 说话人验证器算余弦相似度。

原理：
  campplus.onnx 是 CosyVoice 内部用来提取 speaker embedding 的模型
  （frontend.py:_extract_spk_embedding）。它输出的 192 维向量就是"音色指纹"。
  两段音频的 embedding 余弦相似度，是业界标准的说话人相似度指标。

参考基准（论文/官方评测口径）：
  > 0.85  同一人（不同录音）
  0.70-0.85  很像，可能是同一人
  0.55-0.70  有相似性，但能听出区别
  < 0.55  不像

本脚本回答三个问题：
  Q1 合成音频 vs 参考音频 的相似度是多少？（客观"像不像"）
  Q2 合成音频 vs 参考音频原句合成 的相似度？（音色漂移程度）
  Q3 换不同参考、不同文本长度，相似度如何变化？
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
OFFICIAL = Path("/home/user/CosyVoice/asset/zero_shot_prompt.wav")
REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
CAND = Path("/mnt/d/xiangmudaima/zjy14/_ref_select")
TRIM = Path("/mnt/d/xiangmudaima/zjy14/_trim_test")
RATIO = Path("/mnt/d/xiangmudaima/zjy14/_ratio_test")
DIAG = Path("/mnt/d/xiangmudaima/zjy14/_clone_diag")


def build_session() -> onnxruntime.InferenceSession:
    opt = onnxruntime.SessionOptions()
    opt.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
    opt.intra_op_num_threads = 1
    return onnxruntime.InferenceSession(
        str(CAMPPUS), sess_options=opt, providers=["CPUExecutionProvider"]
    )


def embed(session: onnxruntime.InferenceSession, wav_path: Path) -> np.ndarray:
    """提取 192 维 speaker embedding。

    与 cosyvoice/cli/frontend.py:_extract_spk_embedding 完全一致：
    16kHz 重采样 -> 80 维 fbank（dither=0）-> 减均值 -> campplus.onnx
    """
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


def cos_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def verdict(sim: float) -> str:
    if sim >= 0.85:
        return "同一人（几乎无差别）"
    if sim >= 0.70:
        return "很像"
    if sim >= 0.55:
        return "有相似性但能听出区别"
    return "不像"


def main() -> int:
    sess = build_session()
    print("=" * 100)
    print(" 说话人相似度分析（campplus 192 维 embedding 余弦相似度）")
    print("=" * 100)
    print("  基准: >=0.85 同一人 | 0.70-0.85 很像 | 0.55-0.70 有相似性 | <0.55 不像")

    # ---- 基准：官方参考 vs 官方合成 ----
    print()
    print("-" * 100)
    print(" A. 基准校准：官方参考 vs 官方合成（这是模型能达到的上限）")
    print("-" * 100)

    if OFFICIAL.is_file():
        off_emb = embed(sess, OFFICIAL)
        # 找官方合成的样本
        off_synth = None
        for cand in [
            Path("/mnt/d/xiangmudaima/zjy14/_ref_ab/官方1.wav"),
            Path("/mnt/d/xiangmudaima/zjy14/_ref_ab/官方2.wav"),
        ]:
            if cand.is_file():
                off_synth = cand
                break
        if off_synth:
            s = cos_sim(off_emb, embed(sess, off_synth))
            print(f"  官方参考 vs 官方合成:  {s:.4f}   {verdict(s)}")
        else:
            print("  （未找到官方合成样本）")

    # ---- 当前参考 vs 各种合成 ----
    print()
    print("-" * 100)
    print(" B. 爱莉希雅：参考 vs 合成")
    print("-" * 100)

    ref_emb = embed(sess, REF)
    ref_name = "当前参考(9.7s 亢奋段)"

    samples = [
        ("_audio_probe/00_baseline.wav", "Phase0基线(短文本)"),
        ("_audio_probe/01_happy.wav", "Phase0开心"),
        ("_audio_probe/04_slow.wav", "Phase0慢速"),
        ("_timbre_diag/A1_纯基线_官方路径.wav", "重测基线(短文本)"),
        ("_timbre_diag/D_复述参考文本.wav", "复述参考原句"),
        ("_rl_compare/base_复述.wav", "复述(base模型)"),
        ("_rl_compare/rl_复述.wav", "复述(rl模型)"),
        ("_ratio_test/C_长参考+短文本.wav", "短文本(比值0.21)"),
        ("_ratio_test/B_长参考+长文本.wav", "长文本(比值1.08)"),
    ]
    print(f"  {'样本':<32}{'相似度':>9}   判断")
    print("  " + "-" * 66)
    results = []
    for rel, label in samples:
        p = Path("/mnt/d/xiangmudaima/zjy14") / rel
        if not p.is_file():
            continue
        s = cos_sim(ref_emb, embed(sess, p))
        results.append((label, s))
        print(f"  {label:<32}{s:>9.4f}   {verdict(s)}")

    # ---- 不同候选参考的合成结果 ----
    print()
    print("-" * 100)
    print(" C. 不同参考的合成结果 vs 各自参考（长文本，比值达标）")
    print("-" * 100)

    pairs = []
    for key in ("1.0", "1.4", "1.5", "1.6"):
        orig = CAND / f"cand_{key}.wav"
        trimmed = TRIM / f"trim_{key}.wav"
        synth_orig = TRIM / f"原_{key}.wav"
        synth_trim = TRIM / f"裁_{key}.wav"
        if orig.is_file():
            pairs.append((f"{key}_原始参考", orig, synth_orig))
        if trimmed.is_file():
            pairs.append((f"{key}_裁静音", trimmed, synth_trim))

    print(f"  {'参考':<18}{'参考自相似':>11}{'合成vs参考':>12}   判断")
    print("  " + "-" * 66)
    for label, ref_p, syn_p in pairs:
        if not ref_p.is_file() or not syn_p.is_file():
            continue
        r_emb = embed(sess, ref_p)
        s_emb = embed(sess, syn_p)
        s = cos_sim(r_emb, s_emb)
        print(f"  {label:<18}{'—':>11}{s:>12.4f}   {verdict(s)}")

    # ---- 参考之间的一致性 ----
    print()
    print("-" * 100)
    print(" D. 参考音频之间的一致性（判断素材是否同一人）")
    print("-" * 100)

    refs = [("当前参考", REF)]
    for key in ("1.0", "1.4", "1.5", "1.6"):
        p = CAND / f"cand_{key}.wav"
        if p.is_file():
            refs.append((f"{key}", p))
    if OFFICIAL.is_file():
        refs.append(("官方示例", OFFICIAL))

    embs = {name: embed(sess, p) for name, p in refs}
    names = list(embs)
    print(f"  {'':<12}" + "".join(f"{n:>12}" for n in names))
    print("  " + "-" * (12 + 12 * len(names)))
    for a in names:
        row = f"  {a:<12}"
        for b in names:
            row += f"{cos_sim(embs[a], embs[b]):>12.4f}"
        print(row)

    print()
    print("=" * 100)
    print(" 结论")
    print("=" * 100)
    if results:
        best = max(results, key=lambda r: r[1])
        worst = min(results, key=lambda r: r[1])
        print(f"  与参考最像的合成样本: {best[0]}  ({best[1]:.4f})")
        print(f"  与参考最不像的合成样本: {worst[0]}  ({worst[1]:.4f})")
        print()
        print("  解读：")
        print("    - 若「复述参考原句」相似度明显高于「新文本」，说明模型泛化时音色漂移")
        print("    - 若「长文本」相似度明显高于「短文本」，说明文本长度比是主因")
        print("    - 若所有样本相似度都低，说明参考音频本身不足以表达该音色")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
