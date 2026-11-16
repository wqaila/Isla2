"""关键验证：F0 下降是指令效果，还是「prompt 变长」的副作用？

疑点：
  5 种情绪的 F0 全部低于基线（-10 ~ -32Hz）。
  如果是指令生效，happy 应该升调、sad 应该降调，方向应该不同。
  全部同向下降，说明可能是 prompt_text 变长导致的系统性影响。

验证设计（用「无意义指令」作安慰剂对照）：
  P1 基线：prompt = 参考文本
  P2 安慰剂：prompt = "请用蓝色的语气说一句话。" + 参考文本
     —— 这条指令无实际情绪含义，若 F0 也下降，说明下降是 prompt 变长导致的
  P3 真指令 happy
  P4 真指令 sad

若 P2 的 F0 与 P3/P4 相近 -> 指令无效，F0 变化只是 prompt 长度效应
若 P2 的 F0 接近 P1，而 P3/P4 明显不同 -> 指令有效
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
import soundfile as sf  # noqa: E402

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
REF_WAV = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
REF_TXT = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_d1_placebo")
OUT.mkdir(parents=True, exist_ok=True)

SR = 24000
N = 5  # 每组 5 次，提高统计可靠性
TEXT = "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。"

CASES = {
    "P1_基线": "",
    "P2_安慰剂_蓝色": "请用蓝色的语气说一句话。",
    "P3_开心": "请非常开心地说一句话。",
    "P4_伤心": "请非常伤心地说一句话。",
}


def voiced(x: np.ndarray, sr: int) -> tuple[float, float]:
    hop = int(sr * 0.01)
    n = len(x) // hop
    if n == 0:
        return 0.0, 1.0
    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    pk = float(rms.max())
    if pk <= 0:
        return 0.0, 1.0
    v = rms > max(pk * 0.02, 1e-6)
    idx = np.where(v)[0]
    if idx.size == 0:
        return 0.0, 1.0
    inner = v[idx[0]: idx[-1] + 1]
    return float(inner.sum()) * 0.01, float((~inner).mean())


def f0(x: np.ndarray, sr: int, fmin=70.0, fmax=450.0) -> tuple[float, float]:
    win, hop = int(sr * 0.04), int(sr * 0.01)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return 0.0, 0.0
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
    e = np.sqrt((fr**2).mean(axis=1))
    fr = fr[e >= np.percentile(e, 70)]
    if fr.shape[0] == 0:
        return 0.0, 0.0
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
        return 0.0, 0.0
    a = np.array(out)
    return float(np.median(a)), float(np.std(a) / max(np.mean(a), 1e-9))


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3
    from custom_voice.inference.text_frontend import enable_wetext_frontend

    ref_body = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" in ref_body:
        ref_body = ref_body.split("<|endofprompt|>", 1)[1]
    ref_full = "You are a helpful assistant.<|endofprompt|>" + ref_body

    print("=" * 100)
    print(" 安慰剂对照：F0 下降是「指令效果」还是「prompt 变长」？")
    print("=" * 100)

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
            "llm_prompt_speech_token": cached["llm_prompt_speech_token"],
            "llm_prompt_speech_token_len": cached["llm_prompt_speech_token_len"],
        }
        return list(cosy.model.tts(**mi, stream=False, speed=1.0))

    results: dict[str, list[tuple[float, float, float]]] = {}

    for label, core in CASES.items():
        prompt = (ref_full if not core
                  else f"You are a helpful assistant. {core}<|endofprompt|>{ref_body}")
        print(f"\n--- {label} ---")
        vals = []
        for i in range(N):
            reset_hop()
            try:
                chunks = synth(prompt)
            except Exception as exc:  # noqa: BLE001
                print(f"    第{i+1}次失败: {type(exc).__name__}")
                continue
            if not chunks:
                continue
            parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                     for c in chunks]
            y = np.concatenate(parts) if len(parts) > 1 else parts[0]
            sf.write(str(OUT / f"{label}_{i+1}.wav"), y, SR, subtype="PCM_16")

            dur = len(y) / SR
            vd, sil = voiced(y, SR)
            mf0, cv = f0(y, SR)
            vals.append((mf0, cv, dur))
            print(f"    第{i+1}次  F0 {mf0:6.1f}Hz  起伏 {cv:.3f}  {dur:.2f}s")
        if vals:
            results[label] = vals

    # ---- 统计对比 ----
    print()
    print("=" * 100)
    print(" 统计对比（中位数 ± 标准差）")
    print("=" * 100)
    print(f"  {'方案':<20}{'F0 中位':>10}{'F0 标准差':>11}"
          f"{'起伏中位':>10}{'时长中位':>10}")
    print("  " + "-" * 64)

    stats: dict[str, dict] = {}
    for label, vals in results.items():
        f0s = [v[0] for v in vals]
        cvs = [v[1] for v in vals]
        durs = [v[2] for v in vals]
        s = {
            "f0_med": float(np.median(f0s)),
            "f0_std": float(np.std(f0s)),
            "cv_med": float(np.median(cvs)),
            "dur_med": float(np.median(durs)),
        }
        stats[label] = s
        print(f"  {label:<20}{s['f0_med']:>10.1f}{s['f0_std']:>11.1f}"
              f"{s['cv_med']:>10.3f}{s['dur_med']:>9.2f}s")

    # ---- 判定 ----
    print()
    print("=" * 100)
    print(" 判定")
    print("=" * 100)

    base = stats.get("P1_基线")
    placebo = stats.get("P2_安慰剂_蓝色")
    happy = stats.get("P3_开心")
    sad = stats.get("P4_伤心")

    if base and placebo and happy and sad:
        print(f"  基线 F0        {base['f0_med']:.1f} Hz")
        print(f"  安慰剂 F0      {placebo['f0_med']:.1f} Hz  "
              f"（相对基线 {placebo['f0_med'] - base['f0_med']:+.1f}）")
        print(f"  开心 F0        {happy['f0_med']:.1f} Hz  "
              f"（相对基线 {happy['f0_med'] - base['f0_med']:+.1f}）")
        print(f"  伤心 F0        {sad['f0_med']:.1f} Hz  "
              f"（相对基线 {sad['f0_med'] - base['f0_med']:+.1f}）")
        print()

        # 关键判据：真指令之间的差异 vs 安慰剂
        d_happy_sad = abs(happy["f0_med"] - sad["f0_med"])
        d_placebo_base = abs(placebo["f0_med"] - base["f0_med"])
        noise = max(base["f0_std"], placebo["f0_std"], 1.0)

        print(f"  开心 vs 伤心 的 F0 差: {d_happy_sad:.1f} Hz")
        print(f"  安慰剂 vs 基线 的 F0 差: {d_placebo_base:.1f} Hz")
        print(f"  组内噪声（标准差）: {noise:.1f} Hz")
        print()

        if d_happy_sad > max(d_placebo_base * 2, noise * 2):
            print("  [结论] 开心与伤心的差异显著大于安慰剂效应 -> 指令可能有效")
            print("         但仍需人工盲听确认")
        else:
            print("  [结论] 开心与伤心的差异未超过安慰剂效应")
            print("         -> 之前观察到的 F0 变化主要是 prompt 变长导致，不是情绪指令")
            print("         -> 情绪指令实际未生效")

        # 方向性检查：情绪应有相反方向
        happy_dir = happy["f0_med"] - base["f0_med"]
        sad_dir = sad["f0_med"] - base["f0_med"]
        if happy_dir * sad_dir < 0:
            print()
            print(f"  [加分] 开心({happy_dir:+.1f}) 与 伤心({sad_dir:+.1f}) 方向相反，"
                  "符合情绪直觉")
        else:
            print()
            print(f"  [减分] 开心({happy_dir:+.1f}) 与 伤心({sad_dir:+.1f}) 同向，"
                  "不符合情绪直觉")

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
