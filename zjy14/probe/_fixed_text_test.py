"""用修正后的完整文本重测：验证"文本补全"是否解决音色问题。

用户提供：
  1.5: 睡不着的话就来聊聊天吧，我们还有很多时间，不是吗。
  1.6: 晚上好，如此美妙的夜晚，你想和谁一起度过呢。

原标注各 11 字，现分别 22 / 19 字。

验证三步：
  1. 语速核对：补全后是否回到正常 4-5 字/秒
  2. 相似度对比：补全前 vs 补全后
  3. 与当前参考（0.9208 基准）对比
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
CAMPPUS = MODEL / "camppus.onnx"
CAMPPUS = MODEL / "campplus.onnx"
REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
CAND = Path("/mnt/d/xiangmudaima/zjy14/_ref_select")
TRIM = Path("/mnt/d/xiangmudaima/zjy14/_trim_test")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_fixed_text")
OUT.mkdir(parents=True, exist_ok=True)
SR = 24000
PREFIX = "You are a helpful assistant.<|endofprompt|>"

# 用户提供的完整文本
FIXED = {
    "1.5": "睡不着的话就来聊聊天吧，我们还有很多时间，不是吗。",
    "1.6": "晚上好，如此美妙的夜晚，你想和谁一起度过呢。",
}
# 原（残缺）标注
OLD = {
    "1.5": "睡不着的话就来聊聊天吧",
    "1.6": "晚上好，如此美妙的夜晚",
}

# 合成文本：长文本保证比值达标
SYNTH = (
    "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨，"
    "窗外的树叶被打得七零八落，今天总算放晴了。"
)


def n_chars(t: str) -> int:
    """去掉标点后的字数。"""
    return len([c for c in t if c not in "，。！？、；：,.!?;: "])


def voiced_duration(x: np.ndarray, sr: int) -> float:
    hop = int(sr * 0.01)
    n = len(x) // hop
    if n == 0:
        return 0.0
    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return 0.0
    voiced = rms > max(peak * 0.02, 1e-6)
    idx = np.where(voiced)[0]
    if idx.size == 0:
        return 0.0
    return float(voiced[idx[0]: idx[-1] + 1].sum()) * 0.01


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


def main() -> int:
    print("=" * 104)
    print(" 第一步：语速核对（补全后是否回到正常范围）")
    print("=" * 104)
    print("  正常中文语速 4-5 字/秒\n")
    print(f"  {'音频':<14}{'语音段':>8}{'旧字数':>7}{'旧语速':>8}"
          f"{'新字数':>7}{'新语速':>8}  判断")
    print("  " + "-" * 76)

    for key in ("1.5", "1.6"):
        p = TRIM / f"trim_{key}.wav"
        if not p.is_file():
            p = CAND / f"cand_{key}.wav"
        x, sr = sf.read(str(p), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        vd = voiced_duration(x, sr)
        n_old, n_new = n_chars(OLD[key]), n_chars(FIXED[key])
        r_old = n_old / vd if vd > 0 else 0
        r_new = n_new / vd if vd > 0 else 0
        judge = "正常" if 3.5 <= r_new <= 5.5 else ("偏慢" if r_new < 3.5 else "偏快")
        print(f"  {key:<14}{vd:>7.2f}s{n_old:>7}{r_old:>8.2f}{n_new:>7}{r_new:>8.2f}  {judge}")

    print()
    print("=" * 104)
    print(" 第二步：合成 + 相似度（补全前 vs 补全后）")
    print("=" * 104)

    from cosyvoice.cli.cosyvoice import CosyVoice3

    sess = build_session()
    cosy = CosyVoice3(MODEL, fp16=False)
    print(f"  text_frontend = {cosy.frontend.text_frontend!r}")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    # 基准：当前参考
    ref_emb = embed(sess, REF)
    print("\n  基准：当前参考（已知相似度 0.9208）")
    print(f"  合成文本 {n_chars(SYNTH)} 字")

    rows = []
    for key in ("1.5", "1.6"):
        for tag, txt in (("补全前", OLD[key]), ("补全后", FIXED[key])):
            wav = TRIM / f"trim_{key}.wav"
            if not wav.is_file():
                wav = CAND / f"cand_{key}.wav"
            prompt = PREFIX + txt
            ratio = n_chars(SYNTH) / n_chars(prompt)
            reset_hop()
            try:
                chunks = list(cosy.inference_zero_shot(
                    SYNTH, prompt, str(wav), zero_shot_spk_id="", stream=False))
            except Exception as exc:  # noqa: BLE001
                print(f"  [{key}_{tag}] 失败: {type(exc).__name__}: {exc}")
                continue
            if not chunks:
                continue
            parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                     for c in chunks]
            y = np.concatenate(parts) if len(parts) > 1 else parts[0]
            out_wav = OUT / f"{key}_{tag}.wav"
            sf.write(str(out_wav), y, SR, subtype="PCM_16")

            # 与自身参考的相似度
            s_self = sim(embed(sess, wav), embed(sess, out_wav))
            # 与当前参考的相似度（衡量是否更像"原始角色"）
            s_cur = sim(ref_emb, embed(sess, out_wav))
            rows.append((f"{key}_{tag}", s_self, s_cur, ratio, len(y) / SR))
            print(f"  {key}_{tag:<10} 与自身参考 {s_self:.4f}   "
                  f"与当前参考 {s_cur:.4f}   比值 {ratio:.2f}   {len(y)/SR:.1f}s")

    print()
    print("=" * 104)
    print(" 第三步：结论")
    print("=" * 104)
    print(f"  {'样本':<14}{'与自身参考':>12}{'与当前参考':>12}{'文本比值':>10}   判断")
    print("  " + "-" * 62)
    for label, s_self, s_cur, ratio, dur in rows:
        v = "同一人" if s_self >= 0.85 else ("很像" if s_self >= 0.70 else "有相似性")
        print(f"  {label:<14}{s_self:>12.4f}{s_cur:>12.4f}{ratio:>10.2f}   {v}")

    # 提升幅度
    for key in ("1.5", "1.6"):
        old = next((r for r in rows if r[0] == f"{key}_补全前"), None)
        new = next((r for r in rows if r[0] == f"{key}_补全后"), None)
        if old and new:
            d = new[1] - old[1]
            print(f"\n  {key}: 相似度 {old[1]:.4f} -> {new[1]:.4f}  "
                  f"({'提升' if d > 0 else '下降'} {abs(d):+.4f})")

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
