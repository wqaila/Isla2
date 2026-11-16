"""D1 正式验证：用 W2 组合 prompt 方案跑 6 种情绪。

方案（已实测速度正常）：
  prompt_text = "You are a helpful assistant. <指令核心><|endofprompt|><参考正文>"
  保留 llm_prompt_speech_token（删掉会慢 2.5 倍）

关键设计：
  1. 每组 3 次采样取中位数（单次不可靠）
  2. 严格同一合成文本、同一参考
  3. 导出盲听样本，命名清晰便于 A/B
  4. 计算「情绪间区分度」——如果 6 种情绪的指标几乎重合，说明指令无效

判定标准（客观）：
  若各情绪的 F0/起伏/语速 差异都在噪声范围内 -> 指令未生效
  若至少 3 种情绪有明确差异 -> 指令部分生效，需人工确认听感
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")
sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
REF_WAV = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
REF_TXT = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_d1_final")
OUT.mkdir(parents=True, exist_ok=True)

SR = 24000
N_SAMPLES = 3

# 合成文本 29 字；参考正文 42 字；比值 0.69 > 0.5
TEXT = "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。"

# 6 种情绪的指令核心（不含前缀）
EMOTIONS: dict[str, dict] = {
    "neutral": {"core": "", "official": True, "note": "无指令基线"},
    "happy": {"core": "请非常开心地说一句话。", "official": True, "note": "官方"},
    "sad": {"core": "请非常伤心地说一句话。", "official": True, "note": "官方"},
    "angry": {"core": "请非常生气地说一句话。", "official": True, "note": "官方"},
    "comforting": {"core": "请用温柔安慰的语气说一句话。", "official": False,
                   "note": "自造·实验性"},
    "playful": {"core": "请用俏皮活泼的语气说一句话。", "official": False,
                "note": "自造·实验性"},
}


def count_chars(t: str) -> int:
    return len(t.replace(" ", ""))


def voiced_stats(x: np.ndarray, sr: int) -> tuple[float, float]:
    hop = int(sr * 0.01)
    n = len(x) // hop
    if n == 0:
        return 0.0, 1.0
    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return 0.0, 1.0
    voiced = rms > max(peak * 0.02, 1e-6)
    idx = np.where(voiced)[0]
    if idx.size == 0:
        return 0.0, 1.0
    inner = voiced[idx[0]: idx[-1] + 1]
    return float(inner.sum()) * 0.01, float((~inner).mean())


def f0_stats(x: np.ndarray, sr: int, fmin=70.0, fmax=450.0) -> dict:
    win, hop = int(sr * 0.04), int(sr * 0.01)
    n = max(0, (len(x) - win) // hop + 1)
    empty = {"f0": 0.0, "f0cv": 0.0, "f0_p10": 0.0, "f0_p90": 0.0}
    if n <= 0:
        return empty
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
    e = np.sqrt((fr**2).mean(axis=1))
    fr = fr[e >= np.percentile(e, 70)]
    if fr.shape[0] == 0:
        return empty
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
    if not out:
        return empty
    arr = np.array(out)
    return {
        "f0": float(np.median(arr)),
        "f0cv": float(np.std(arr) / max(np.mean(arr), 1e-9)),
        "f0_p10": float(np.percentile(arr, 10)),
        "f0_p90": float(np.percentile(arr, 90)),
    }


def env_stats(x: np.ndarray, sr: int) -> dict:
    win, hop = int(sr * 0.04), int(sr * 0.01)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return {"env_cv": 0.0, "rms_db": -99.0}
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
    e = np.sqrt((fr**2).mean(axis=1))
    voiced = e[e > e.max() * 0.05] if e.max() > 0 else e
    env_cv = float(voiced.std() / max(voiced.mean(), 1e-9)) if voiced.size else 0.0
    rms = float(np.sqrt((x.astype(np.float64) ** 2).mean()))
    return {"env_cv": env_cv, "rms_db": float(20 * np.log10(max(rms, 1e-9)))}


def syl_stats(x: np.ndarray, sr: int) -> dict:
    win, hop = int(sr * 0.04), int(sr * 0.01)
    n = max(0, (len(x) - win) // hop + 1)
    if n < 10:
        return {"syl_cv": 0.0, "syl_n": 0}
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
    e = np.sqrt((fr**2).mean(axis=1))
    if e.max() <= 0:
        return {"syl_cv": 0.0, "syl_n": 0}
    voiced = e > e.max() * 0.05
    vi = np.where(voiced)[0]
    if vi.size == 0:
        return {"syl_cv": 0.0, "syl_n": 0}
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
        return {"syl_cv": 0.0, "syl_n": len(lengths)}
    arr = np.array(lengths)
    return {"syl_cv": float(arr.std() / max(arr.mean(), 1e-9)), "syl_n": len(lengths)}


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3
    from custom_voice.inference.text_frontend import enable_wetext_frontend

    ref_body = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" in ref_body:
        ref_body = ref_body.split("<|endofprompt|>", 1)[1]
    ref_full = "You are a helpful assistant.<|endofprompt|>" + ref_body

    print("=" * 104)
    print(" D1 情绪验证（W2 组合 prompt 方案）")
    print("=" * 104)
    print(f"  合成文本 {count_chars(TEXT)} 字符 / 参考正文 {count_chars(ref_body)} 字符"
          f" / 比值 {count_chars(TEXT)/count_chars(ref_body):.2f}")
    print(f"  每种情绪 {N_SAMPLES} 次采样")

    cosy = CosyVoice3(str(MODEL), fp16=False)
    enable_wetext_frontend(cosy.frontend)
    cosy.add_zero_shot_spk(ref_full, str(REF_WAV), "elysia")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    reset_hop()
    list(cosy.inference_zero_shot("你好。", "", str(REF_WAV),
                                  zero_shot_spk_id="elysia", stream=False))

    cached = dict(cosy.frontend.spk2info["elysia"])
    nc = count_chars(TEXT)

    def synth(prompt_str: str):
        ttk, ttl = cosy.frontend._extract_text_token(TEXT)
        ptk, ptl = cosy.frontend._extract_text_token(prompt_str)
        mi = {
            "text": ttk, "text_len": ttl,
            "prompt_text": ptk, "prompt_text_len": ptl,
            "flow_prompt_speech_token": cached["flow_prompt_speech_token"],
            "flow_prompt_speech_token_len": cached["flow_prompt_speech_token_len"],
            "prompt_speech_feat": cached["prompt_speech_feat"],
            "prompt_speech_feat_len": cached["prompt_speech_feat_len"],
            "llm_embedding": cached["llm_embedding"],
            "flow_embedding": cached["flow_embedding"],
            # 关键：必须保留，删掉会慢 2.5 倍
            "llm_prompt_speech_token": cached["llm_prompt_speech_token"],
            "llm_prompt_speech_token_len": cached["llm_prompt_speech_token_len"],
        }
        return list(cosy.model.tts(**mi, stream=False, speed=1.0))

    results: dict[str, list[dict]] = {}

    for emotion, spec in EMOTIONS.items():
        print(f"\n--- {emotion}  {spec['note']} ---")
        core = spec["core"]
        if core:
            prompt = f"You are a helpful assistant. {core}<|endofprompt|>{ref_body}"
            print(f"    指令: {core}")
        else:
            prompt = ref_full
            print("    指令: （无，基线）")

        samples = []
        for i in range(N_SAMPLES):
            reset_hop()
            try:
                chunks = synth(prompt)
            except Exception as exc:  # noqa: BLE001
                print(f"    第{i+1}次失败: {type(exc).__name__}: {str(exc)[:60]}")
                continue
            if not chunks:
                continue
            parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                     for c in chunks]
            y = np.concatenate(parts) if len(parts) > 1 else parts[0]
            path = OUT / f"{emotion}_{i+1}.wav"
            sf.write(str(path), y, SR, subtype="PCM_16")

            dur = len(y) / SR
            vd, sil = voiced_stats(y, SR)
            d = {
                "dur": round(dur, 2),
                "voiced": round(vd, 2),
                "silence": round(sil, 3),
                "rate": round(nc / max(vd, 0.01), 2),
                **{k: round(v, 3) for k, v in f0_stats(y, SR).items()},
                **{k: round(v, 3) for k, v in env_stats(y, SR).items()},
                **{k: round(v, 3) for k, v in syl_stats(y, SR).items()},
                "file": path.name,
            }
            samples.append(d)
            print(f"    第{i+1}次  {d['dur']:5.2f}s  F0 {d['f0']:6.1f}Hz  "
                  f"起伏 {d['f0cv']:.3f}  能量CV {d['env_cv']:.3f}  "
                  f"音节CV {d['syl_cv']:.3f}  语速 {d['rate']:.2f}")

        if samples:
            results[emotion] = samples

    # ---- 中位数汇总 ----
    print()
    print("=" * 104)
    print(" 中位数汇总")
    print("=" * 104)
    print(f"  {'情绪':<12}{'时长':>7}{'F0':>9}{'F0范围':>14}{'起伏':>8}"
          f"{'能量CV':>9}{'音节CV':>9}{'语速':>8}  来源")
    print("  " + "-" * 92)

    summary = {}
    for emotion, samples in results.items():
        med = {k: float(np.median([s[k] for s in samples]))
               for k in ("dur", "f0", "f0cv", "env_cv", "syl_cv", "rate",
                         "f0_p10", "f0_p90")}
        summary[emotion] = med
        print(f"  {emotion:<12}{med['dur']:>6.2f}s{med['f0']:>9.1f}"
              f"{med['f0_p10']:>6.0f}-{med['f0_p90']:<7.0f}"
              f"{med['f0cv']:>8.3f}{med['env_cv']:>9.3f}"
              f"{med['syl_cv']:>9.3f}{med['rate']:>8.2f}  {EMOTIONS[emotion]['note']}")

    # ---- 与基线的差异 ----
    print()
    print("=" * 104)
    print(" 相对 neutral 基线的偏离")
    print("=" * 104)

    if "neutral" in summary:
        base = summary["neutral"]
        print(f"  {'情绪':<12}{'F0差':>9}{'起伏差':>9}{'能量CV差':>10}"
              f"{'音节CV差':>10}{'语速差':>9}  显著项")
        print("  " + "-" * 76)

        significant_count = 0
        for emotion, med in summary.items():
            if emotion == "neutral":
                continue
            df0 = med["f0"] - base["f0"]
            dcv = med["f0cv"] - base["f0cv"]
            denv = med["env_cv"] - base["env_cv"]
            dsyl = med["syl_cv"] - base["syl_cv"]
            drate = med["rate"] - base["rate"]

            sig = []
            if abs(df0) > 15:
                sig.append("F0")
            if abs(dcv) > 0.03:
                sig.append("起伏")
            if abs(denv) > 0.05:
                sig.append("能量")
            if abs(dsyl) > 0.08:
                sig.append("节奏")
            if abs(drate) > 0.8:
                sig.append("语速")
            if sig:
                significant_count += 1

            print(f"  {emotion:<12}{df0:>+9.1f}{dcv:>+9.3f}{denv:>+10.3f}"
                  f"{dsyl:>+10.3f}{drate:>+9.2f}  "
                  f"{'、'.join(sig) if sig else '无'}")

        print()
        print(f"  有显著差异的情绪: {significant_count}/5")
        if significant_count >= 3:
            print("  [结论] 至少 3 种情绪在客观指标上有区分 -> 指令部分生效")
            print("         必须人工盲听确认听感是否对应情绪")
        elif significant_count >= 1:
            print("  [结论] 仅少数情绪有差异 -> 指令效果有限")
        else:
            print("  [结论] 所有情绪与基线无差异 -> 指令未生效")

    # ---- 保存 ----
    meta = {
        "method": "W2 组合 prompt：指令核心 + <|endofprompt|> + 参考正文，保留 llm_prompt_speech_token",
        "text": TEXT,
        "ref_body": ref_body,
        "text_ratio": round(count_chars(TEXT) / count_chars(ref_body), 3),
        "samples_per_emotion": N_SAMPLES,
        "emotions": {k: {"core": v["core"], "official": v["official"],
                         "note": v["note"]} for k, v in EMOTIONS.items()},
        "raw": results,
        "median": summary,
    }
    (OUT / "result.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print(f"  输出: {OUT}")
    print(f"  样本: {sum(len(v) for v in results.values())} 个 WAV")
    print()
    print("  >>> 客观指标只能筛掉明显无效的。情绪是否真的表达，必须人工盲听。")
    print("  >>> 建议按顺序听：neutral_1 -> happy_1 -> sad_1 -> angry_1")
    print("  >>>            -> comforting_1 -> playful_1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
