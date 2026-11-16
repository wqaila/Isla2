"""关键验证：能否同时拥有「指令生效」+「生成速度正常」。

已实测（_d1_hybrid_diag）：
  V1 正常 zero_shot（缓存 spk）           5.76s  6.17 字/秒  ✓ 快
  V4 prompt=参考文本 + 保留 llm token     6.16s  6.11 字/秒  ✓ 快
  V2 prompt=参考文本 + 删 llm token      14.72s  2.25 字/秒  ✗ 慢
  V3 prompt=指令     + 删 llm token      14.64s  3.53 字/秒  ✗ 慢

结论：**删掉 llm_prompt_speech_token 是变慢的唯一原因**。

但保留它时，prompt_text 必须与参考音频对应（否则 Phase 0 见过卷积报错）。

新假设（本脚本验证）：
  把 prompt_text 构造为「指令 + 参考文本」的组合：
    "You are a helpful assistant. 请非常开心地说一句话。<|endofprompt|><参考文本>"
  这样：
    - <|endofprompt|> 前是指令，让 LLM 理解情绪意图
    - 后面是参考文本，与 llm_prompt_speech_token 对应
  预期：指令生效 + 生成速度正常

对照矩阵：
  W1 prompt=参考文本 + 保留token（快，但无指令）      = 基线
  W2 prompt=指令+参考文本 + 保留token（假设方案）
  W3 prompt=指令 + 保留token（Phase0 报错的情况）
  W4 官方 instruct2 + 空 spk（官方路径，慢）
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
OUT = Path("/mnt/d/xiangmudaima/zjy14/_d1_combined")
OUT.mkdir(parents=True, exist_ok=True)

PREFIX = "You are a helpful assistant.<|endofprompt|>"
SR = 24000
TEXT = "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。"

# 指令的「纯指令部分」（不含前缀和参考文本）
INSTRUCT_CORE = "请非常开心地说一句话。"


def count_chars(t: str) -> int:
    return len(t.replace(" ", ""))


def voiced_duration(x: np.ndarray, sr: int) -> tuple[float, float]:
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


def f0_stats(x: np.ndarray, sr: int, fmin=70.0, fmax=450.0) -> tuple[float, float]:
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
        seg = ac[lo:min(hi + 1, len(ac))]
        if seg.size == 0:
            continue
        pk = int(np.argmax(seg)) + lo
        if ac[pk] < 0.3 * ac[0]:
            continue
        out.append(sr / pk)
    if not out:
        return 0.0, 0.0
    arr = np.array(out)
    return float(np.median(arr)), float(np.std(arr) / max(np.mean(arr), 1e-9))


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3
    from custom_voice.inference.text_frontend import enable_wetext_frontend

    ref_body = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" in ref_body:
        ref_body = ref_body.split("<|endofprompt|>", 1)[1]
    ref_full = PREFIX + ref_body

    print("=" * 104)
    print(" 验证：指令 + 参考文本组合 prompt")
    print("=" * 104)
    print(f"  合成文本  {count_chars(TEXT)} 字符")
    print(f"  参考正文  {count_chars(ref_body)} 字符")
    print(f"  指令核心  {count_chars(INSTRUCT_CORE)} 字符")

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

    print()
    print(f"  {'方案':<36}{'时长':>8}{'有声':>7}{'静音%':>7}{'语速':>8}"
          f"{'F0':>9}{'起伏':>7}")
    print("  " + "-" * 84)

    def run(label: str, prompt_text_str: str, keep_llm: bool) -> dict | None:
        ttk, ttl = cosy.frontend._extract_text_token(TEXT)
        ptk, ptl = cosy.frontend._extract_text_token(prompt_text_str)
        mi = {
            "text": ttk, "text_len": ttl,
            "prompt_text": ptk, "prompt_text_len": ptl,
            "flow_prompt_speech_token": cached["flow_prompt_speech_token"],
            "flow_prompt_speech_token_len": cached["flow_prompt_speech_token_len"],
            "prompt_speech_feat": cached["prompt_speech_feat"],
            "prompt_speech_feat_len": cached["prompt_speech_feat_len"],
            "llm_embedding": cached["llm_embedding"],
            "flow_embedding": cached["flow_embedding"],
        }
        if keep_llm:
            mi["llm_prompt_speech_token"] = cached["llm_prompt_speech_token"]
            mi["llm_prompt_speech_token_len"] = cached["llm_prompt_speech_token_len"]

        reset_hop()
        try:
            chunks = list(cosy.model.tts(**mi, stream=False, speed=1.0))
        except Exception as exc:  # noqa: BLE001
            print(f"  {label:<36} 失败: {type(exc).__name__}: {str(exc)[:50]}")
            return None
        if not chunks:
            print(f"  {label:<36} 无输出")
            return None

        parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                 for c in chunks]
        y = np.concatenate(parts) if len(parts) > 1 else parts[0]
        safe = label.split()[0]
        sf.write(str(OUT / f"{safe}.wav"), y, SR, subtype="PCM_16")

        dur = len(y) / SR
        vd, sil = voiced_duration(y, SR)
        f0, f0cv = f0_stats(y, SR)
        print(f"  {label:<36}{dur:>7.2f}s{vd:>6.2f}s{sil:>7.1%}"
              f"{nc/max(vd,0.01):>8.2f}{f0:>9.1f}{f0cv:>7.3f}")
        return {"dur": dur, "vd": vd, "sil": sil, "f0": f0, "f0cv": f0cv,
                "rate": nc / max(vd, 0.01)}

    # W1 基线：参考文本 + 保留 token（快，无指令）
    r1 = run("W1 参考文本+留token(基线)", ref_full, True)

    # W2 假设方案：指令+参考文本 + 保留 token
    combined = f"You are a helpful assistant. {INSTRUCT_CORE}<|endofprompt|>{ref_body}"
    r2 = run("W2 指令+参考文本+留token", combined, True)

    # W3 对照：纯指令 + 保留 token（Phase 0 报错的组合）
    r3 = run("W3 纯指令+留token", PREFIX + INSTRUCT_CORE, True)

    # W4 对照：纯指令 + 删 token（已知慢）
    r4 = run("W4 纯指令+删token", PREFIX + INSTRUCT_CORE, False)

    # W5 官方 instruct2 + 空 spk（官方路径）
    reset_hop()
    try:
        chunks = list(cosy.inference_instruct2(
            TEXT, PREFIX + INSTRUCT_CORE, str(REF_WAV),
            zero_shot_spk_id="", stream=False))
        if chunks:
            parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                     for c in chunks]
            y = np.concatenate(parts) if len(parts) > 1 else parts[0]
            sf.write(str(OUT / "W5.wav"), y, SR, subtype="PCM_16")
            dur = len(y) / SR
            vd, sil = voiced_duration(y, SR)
            f0, f0cv = f0_stats(y, SR)
            print(f"  {'W5 官方instruct2+空spk':<36}{dur:>7.2f}s{vd:>6.2f}s"
                  f"{sil:>7.1%}{nc/max(vd,0.01):>8.2f}{f0:>9.1f}{f0cv:>7.3f}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {'W5 官方instruct2+空spk':<36} 失败: {type(exc).__name__}")

    # ---- 结论 ----
    print()
    print("=" * 104)
    print(" 结论")
    print("=" * 104)

    if r1 and r2:
        print(f"  基线 W1（无指令，快）:  {r1['dur']:.2f}s  {r1['rate']:.2f} 字/秒")
        print(f"  方案 W2（指令+参考）:    {r2['dur']:.2f}s  {r2['rate']:.2f} 字/秒")
        speedup = r4["dur"] / r2["dur"] if r4 else 0
        print()
        if r2["dur"] < r1["dur"] * 1.5:
            print("  [成功] W2 时长接近基线，说明组合 prompt 保持了生成效率")
        else:
            print("  [失败] W2 仍明显慢于基线")
        if r4:
            print(f"  相比 W4（纯指令+删token {r4['dur']:.2f}s），W2 快 {speedup:.2f} 倍")
        print()
        print("  关键判断：W2 的 F0 与基线是否有差异（有差异说明指令生效）")
        if r1 and r2:
            print(f"    W1 F0 {r1['f0']:.1f}Hz  ->  W2 F0 {r2['f0']:.1f}Hz  "
                  f"差 {r2['f0'] - r1['f0']:+.1f}Hz")
        print()
        print("  注意：F0 差异不等于情绪表达。最终仍需人工盲听。")

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
