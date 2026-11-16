"""诊断 D1 结果异常：为什么 42 字文本生成了 11-23 秒音频。

现象：
  Phase 2a：30 字文本 -> 5.7s（5.3 字/秒，正常）
  D1 本次：42 字文本 -> 11.7-23.3s（1.8-3.6 字/秒，异常偏慢）

疑点：
  A. 文本被 text_normalize 切成多段，每段单独生成并带首尾静音
  B. 混合调用模式下 instruction 与 flow speech token 不匹配
  C. 长文本本身导致生成退化
  D. 我的 chars_per_s 计算方式有误（未扣除静音）

方法：分别测
  T1 短文本（18字）走正常路径
  T2 中文本（30字）走正常路径
  T3 长文本（42字）走正常路径
  T4 长文本（42字）走混合模式（无指令）
  并检查每段的静音占比
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
OUT = Path("/mnt/d/xiangmudaima/zjy14/_d1_diag")
OUT.mkdir(parents=True, exist_ok=True)

PREFIX = "You are a helpful assistant.<|endofprompt|>"
SR = 24000

TEXTS = {
    "T1_短18字": "今天天气真不错，我们一起出去走走吧。",
    "T2_中30字": "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。",
    "T3_长42字": (
        "今天天气真不错，我们一起出去走走吧。"
        "昨天下了整整一天的雨，窗外的树叶被打得七零八落。"
    ),
}


def n_chars(t: str) -> int:
    return len([c for c in t if c not in "，。！？、；：,.!?;: "]) + len(t) - len(
        [c for c in t if c not in "，。！？、；：,.!?;: "]
    )


def count_chars(t: str) -> int:
    """统计字符数（含标点，用于语速计算）。"""
    return len(t.replace(" ", ""))


def voiced_duration(x: np.ndarray, sr: int) -> tuple[float, float]:
    """返回 (有声时长, 静音占比)。"""
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


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3
    from custom_voice.inference.text_frontend import enable_wetext_frontend

    ref_text = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" not in ref_text:
        ref_text = PREFIX + ref_text

    print("=" * 100)
    print(" D1 异常诊断：文本长度 vs 生成时长")
    print("=" * 100)

    cosy = CosyVoice3(str(MODEL), fp16=False)
    enable_wetext_frontend(cosy.frontend)
    cosy.add_zero_shot_spk(ref_text, str(REF_WAV), "elysia")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    reset_hop()
    list(cosy.inference_zero_shot("你好。", "", str(REF_WAV),
                                  zero_shot_spk_id="elysia", stream=False))

    print(f"\n  参考文本 {len(ref_text)} 字符（含前缀）")
    print()
    print(f"  {'文本':<14}{'字数':>5}{'分段':>5}{'总时长':>8}{'有声':>7}"
          f"{'静音%':>7}{'语速(总)':>9}{'语速(有声)':>11}")
    print("  " + "-" * 76)

    for label, text in TEXTS.items():
        # 先看 text_normalize 会切成几段
        try:
            segs = cosy.frontend.text_normalize(text, split=True, text_frontend=True)
            n_seg = len(segs)
        except Exception:
            segs, n_seg = [text], 1

        reset_hop()
        chunks = list(cosy.inference_zero_shot(
            text, "", str(REF_WAV), zero_shot_spk_id="elysia", stream=False))
        if not chunks:
            print(f"  {label:<14} 无输出")
            continue

        parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                 for c in chunks]
        y = np.concatenate(parts) if len(parts) > 1 else parts[0]
        sf.write(str(OUT / f"{label}.wav"), y, SR, subtype="PCM_16")

        dur = len(y) / SR
        vd, sil = voiced_duration(y, SR)
        nc = count_chars(text)
        print(
            f"  {label:<14}{nc:>5}{n_seg:>5}{dur:>7.2f}s{vd:>6.2f}s"
            f"{sil:>7.1%}{nc/dur:>9.2f}{nc/max(vd,0.01):>11.2f}"
        )

        # 打印分段内容
        if n_seg > 1:
            for i, s in enumerate(segs):
                print(f"      段{i+1}: {s[:50]}")

    # ---- 混合模式对照 ----
    print()
    print("=" * 100)
    print(" 混合模式 vs 正常路径（同一长文本）")
    print("=" * 100)

    text = TEXTS["T3_长42字"]
    cached = dict(cosy.frontend.spk2info["elysia"])

    def hybrid(text: str, instruction: str):
        ttk, ttl = cosy.frontend._extract_text_token(text)
        if instruction:
            itk, itl = cosy.frontend._extract_text_token(instruction)
        else:
            itk, itl = cached["prompt_text"], cached["prompt_text_len"]
        mi = {
            "text": ttk, "text_len": ttl,
            "prompt_text": itk, "prompt_text_len": itl,
            "flow_prompt_speech_token": cached["flow_prompt_speech_token"],
            "flow_prompt_speech_token_len": cached["flow_prompt_speech_token_len"],
            "prompt_speech_feat": cached["prompt_speech_feat"],
            "prompt_speech_feat_len": cached["prompt_speech_feat_len"],
            "llm_embedding": cached["llm_embedding"],
            "flow_embedding": cached["flow_embedding"],
        }
        return list(cosy.model.tts(**mi, stream=False, speed=1.0))

    print(f"\n  {'模式':<26}{'时长':>8}{'有声':>7}{'静音%':>7}{'语速(有声)':>11}")
    print("  " + "-" * 60)

    for label, instr in (
        ("正常路径(zero_shot)", None),
        ("混合_无指令", ""),
        ("混合_开心指令", PREFIX + "请非常开心地说一句话。"),
    ):
        reset_hop()
        try:
            if instr is None:
                chunks = list(cosy.inference_zero_shot(
                    text, "", str(REF_WAV), zero_shot_spk_id="elysia", stream=False))
            else:
                chunks = hybrid(text, instr)
        except Exception as exc:  # noqa: BLE001
            print(f"  {label:<26} 失败: {type(exc).__name__}: {exc}")
            continue
        if not chunks:
            print(f"  {label:<26} 无输出")
            continue
        parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                 for c in chunks]
        y = np.concatenate(parts) if len(parts) > 1 else parts[0]
        sf.write(str(OUT / f"cmp_{label}.wav"), y, SR, subtype="PCM_16")
        dur = len(y) / SR
        vd, sil = voiced_duration(y, SR)
        nc = count_chars(text)
        print(f"  {label:<26}{dur:>7.2f}s{vd:>6.2f}s{sil:>7.1%}{nc/max(vd,0.01):>11.2f}")

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
