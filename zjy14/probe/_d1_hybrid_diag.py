"""深挖混合调用模式为什么生成变慢。

现象（已实测）：
  正常 zero_shot：      8.04s 音频（42 字，6.77 字/秒有声）
  混合_无指令：        17.60s 音频（2.91 字/秒有声）  <- 慢 2.2 倍
  混合_开心指令：      12.04s 音频（4.44 字/秒有声）

推测：
  H1 prompt_text 用了参考文本但删掉了 llm_prompt_speech_token，
     导致 LLM 缺少语音前缀，只能自由发挥 -> 生成冗长
  H2 混合模式传入的 prompt_text 长度（85 字符）远长于合成文本（42 字符），
     模型倾向于生成与 prompt 等长的内容
  H3 flow 条件与 LLM 输出不匹配，导致 Flow 阶段插入额外静音

验证设计：
  V1 正常 zero_shot（基准）
  V2 混合 + 保留 llm_prompt_speech_token（已知会报错，但看是否只在特定情况）
  V3 混合 + 用「空 prompt_text」
  V4 混合 + 用「短 prompt_text」（如单个指令词）
  V5 官方 instruct2 + 空 spk_id（每次都重编码参考）
  V6 官方 instruct2 + 缓存 spk_id（已知指令失效，看时长）
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
OUT = Path("/mnt/d/xiangmudaima/zjy14/_d1_hybrid_diag")
OUT.mkdir(parents=True, exist_ok=True)

PREFIX = "You are a helpful assistant.<|endofprompt|>"
SR = 24000
TEXT = "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。"
INSTR = PREFIX + "请非常开心地说一句话。"


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


def f0med(x: np.ndarray, sr: int, fmin=70.0, fmax=450.0) -> float:
    win, hop = int(sr * 0.04), int(sr * 0.01)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return 0.0
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
    e = np.sqrt((fr**2).mean(axis=1))
    fr = fr[e >= np.percentile(e, 70)]
    if fr.shape[0] == 0:
        return 0.0
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
    return float(np.median(out)) if out else 0.0


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3
    from custom_voice.inference.text_frontend import enable_wetext_frontend

    ref_text = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" not in ref_text:
        ref_text = PREFIX + ref_text

    print("=" * 104)
    print(" 混合调用模式问题定位")
    print("=" * 104)
    print(f"  合成文本 {count_chars(TEXT)} 字符")
    print(f"  参考文本 {count_chars(ref_text)} 字符")
    print(f"  指令文本 {count_chars(INSTR)} 字符")

    cosy = CosyVoice3(str(MODEL), fp16=False)
    enable_wetext_frontend(cosy.frontend)
    cosy.add_zero_shot_spk(ref_text, str(REF_WAV), "elysia")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    reset_hop()
    list(cosy.inference_zero_shot("你好。", "", str(REF_WAV),
                                  zero_shot_spk_id="elysia", stream=False))

    cached = dict(cosy.frontend.spk2info["elysia"])
    nc = count_chars(TEXT)

    print()
    print(f"  {'方案':<34}{'时长':>8}{'有声':>7}{'静音%':>7}{'语速':>8}{'F0':>9}")
    print("  " + "-" * 78)

    def measure(label: str, chunks) -> None:  # noqa: ANN001
        if not chunks:
            print(f"  {label:<34} 无输出")
            return
        parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                 for c in chunks]
        y = np.concatenate(parts) if len(parts) > 1 else parts[0]
        safe = label.replace("/", "_").replace(" ", "_")
        sf.write(str(OUT / f"{safe}.wav"), y, SR, subtype="PCM_16")
        dur = len(y) / SR
        vd, sil = voiced_duration(y, SR)
        print(
            f"  {label:<34}{dur:>7.2f}s{vd:>6.2f}s{sil:>7.1%}"
            f"{nc/max(vd,0.01):>8.2f}{f0med(y, SR):>9.1f}"
        )

    def hybrid(prompt_text: str, prompt_len: int, keep_llm_token: bool,
               text: str = TEXT):
        ttk, ttl = cosy.frontend._extract_text_token(text)
        mi = {
            "text": ttk, "text_len": ttl,
            "prompt_text": prompt_text, "prompt_text_len": prompt_len,
            "flow_prompt_speech_token": cached["flow_prompt_speech_token"],
            "flow_prompt_speech_token_len": cached["flow_prompt_speech_token_len"],
            "prompt_speech_feat": cached["prompt_speech_feat"],
            "prompt_speech_feat_len": cached["prompt_speech_feat_len"],
            "llm_embedding": cached["llm_embedding"],
            "flow_embedding": cached["flow_embedding"],
        }
        if keep_llm_token:
            mi["llm_prompt_speech_token"] = cached["llm_prompt_speech_token"]
            mi["llm_prompt_speech_token_len"] = cached["llm_prompt_speech_token_len"]
        return list(cosy.model.tts(**mi, stream=False, speed=1.0))

    # V1 基准
    reset_hop()
    measure("V1 正常 zero_shot", list(cosy.inference_zero_shot(
        TEXT, "", str(REF_WAV), zero_shot_spk_id="elysia", stream=False)))

    # V2 混合 + 参考文本作 prompt + 删 llm token（之前用的方案）
    reset_hop()
    measure("V2 混合_参考文本_删token", hybrid(
        cached["prompt_text"], cached["prompt_text_len"], False))

    # V3 混合 + 指令作 prompt + 删 llm token
    itk, itl = cosy.frontend._extract_text_token(INSTR)
    reset_hop()
    measure("V3 混合_指令_删token", hybrid(itk, itl, False))

    # V4 混合 + 保留 llm token（已知可能报错）
    reset_hop()
    try:
        measure("V4 混合_参考文本_留token", hybrid(
            cached["prompt_text"], cached["prompt_text_len"], True))
    except Exception as exc:  # noqa: BLE001
        print(f"  {'V4 混合_参考文本_留token':<34} 失败: {type(exc).__name__}")

    # V5 官方 instruct2 + 空 spk_id（重编码参考音频）
    reset_hop()
    measure("V5 官方instruct2_空spk", list(cosy.inference_instruct2(
        TEXT, INSTR, str(REF_WAV), zero_shot_spk_id="", stream=False)))

    # V6 官方 instruct2 + 缓存 spk_id（已知指令失效）
    reset_hop()
    measure("V6 官方instruct2_缓存spk", list(cosy.inference_instruct2(
        TEXT, INSTR, str(REF_WAV), zero_shot_spk_id="elysia", stream=False)))

    # V7 短 prompt_text（只用一个字）
    short_tk, short_tl = cosy.frontend._extract_text_token("说")
    reset_hop()
    measure("V7 混合_单字prompt_删token", hybrid(short_tk, short_tl, False))

    print()
    print("=" * 104)
    print(" 结论分析")
    print("=" * 104)
    print("  关键对比：V1（正常）vs V2（混合）")
    print("  若 V2 时长约为 V1 的 2 倍，说明删掉 llm_prompt_speech_token 后")
    print("  LLM 缺少语音前缀约束，倾向于生成冗长内容。")
    print()
    print("  V5/V6 用于验证官方 instruct2 的真实行为：")
    print("  V5 空 spk_id 会重编码参考音频（慢但正确）")
    print("  V6 缓存 spk_id 会丢弃指令（Phase 0 已证实）")
    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
