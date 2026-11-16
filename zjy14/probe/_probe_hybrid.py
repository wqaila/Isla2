"""Phase 0 探针 3：验证"缓存条件 + 情绪指令"混合模式。

已证实的两个事实：
  E2  instruct2 + zero_shot_spk_id=<缓存>  → 指令失效，且音频异常拉长 3.5 倍
  E1  instruct2 + zero_shot_spk_id=''      → 指令生效，但每次重编码参考音频

根因（读源码得出）：
  frontend_zero_shot() 在 zero_shot_spk_id != '' 时直接返回 spk2info 缓存，
  完全丢弃传入的 prompt_text（即 instruct_text）。
  frontend_instruct2() 随后删掉 llm_prompt_speech_token，
  但 prompt_text 仍是缓存里的参考文本 → LLM 端文本与语音 token 错位 → 音频拉长。

本探针验证修复方案：手动构造 model_input，
  用缓存条件 + 把 prompt_text 替换为 instruct_text。
  分别测试"保留 llm_prompt_speech_token"与"删除"两种变体。
"""
from __future__ import annotations

import sys
import time
import warnings

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")

import numpy as np  # noqa: E402
import torch  # noqa: E402

MODEL = "/home/user/models/Fun-CosyVoice3-0.5B-2512"
REF_WAV = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav"
REF_TXT = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt"
PREFIX = "You are a helpful assistant.<|endofprompt|>"

from cosyvoice.cli.cosyvoice import CosyVoice3  # noqa: E402

ref_text = open(REF_TXT, encoding="utf-8").read().strip()
if "<|endofprompt|>" not in ref_text:
    ref_text = PREFIX + ref_text

print("=" * 64)
cosy = CosyVoice3(MODEL, fp16=False)
cosy.add_zero_shot_spk(ref_text, REF_WAV, "elysia")
print("模型就绪，音色已注册")

TEXT = "今天天气真不错，我们一起出去走走吧。"


def reset_hop():
    if hasattr(cosy.model, "token_hop_len"):
        cosy.model.token_hop_len = 25


def measure(label, chunks, elapsed):
    x = chunks[0]["tts_speech"].detach().cpu().float().numpy().squeeze()
    dur = len(x) / cosy.sample_rate
    rms = float(np.sqrt(np.mean(x**2)))
    zcr = float(np.mean(np.abs(np.diff(np.sign(x))) > 0))
    seg = int(cosy.sample_rate * 0.5)
    env = [float(np.sqrt(np.mean(x[i:i+seg]**2))) for i in range(0, max(1, len(x)-seg), seg)]
    env_std = float(np.std(env)) if env else 0.0
    rtf = elapsed / dur if dur else 0
    print(f"  [{label}] 音频{dur:.2f}s 耗时{elapsed:.2f}s RTF{rtf:.3f} "
          f"RMS{rms:.4f} ZCR{zcr:.4f} 包络std{env_std:.4f}")
    return {"label": label, "dur": dur, "el": elapsed, "rtf": rtf,
            "rms": rms, "zcr": zcr, "env_std": env_std}


def synth_hybrid(label, instruct_text, drop_llm_token):
    """混合模式：缓存条件 + 替换 prompt_text 为指令文本。"""
    reset_hop()
    t0 = time.time()
    try:
        # 取缓存的 speaker 条件（含 embedding / prompt_feat / speech_token）
        cached = dict(cosy.frontend.spk2info["elysia"])
        # 文本 token（待合成）
        ttk, ttl = cosy.frontend._extract_text_token(TEXT)
        # 指令 token
        itk, itl = cosy.frontend._extract_text_token(instruct_text)
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
        if not drop_llm_token:
            mi["llm_prompt_speech_token"] = cached["llm_prompt_speech_token"]
            mi["llm_prompt_speech_token_len"] = cached["llm_prompt_speech_token_len"]
        chunks = list(cosy.model.tts(**mi, stream=False, speed=1.0))
    except Exception as exc:  # noqa: BLE001
        print(f"  [{label}] 失败: {type(exc).__name__}: {exc}")
        return None
    if not chunks:
        print(f"  [{label}] 无输出")
        return None
    return measure(label, chunks, time.time() - t0)


print("=" * 64)
print("F0 基线（缓存条件，无指令）")
res = []
reset_hop()
t0 = time.time()
ch = list(cosy.inference_zero_shot(TEXT, "", REF_WAV, zero_shot_spk_id="elysia", stream=False))
res.append(measure("F0基线", ch, time.time() - t0))

print("=" * 64)
print("F1 混合模式：保留 llm_prompt_speech_token（音色条件完整）")
for label, instr in [
    ("F1开心", "You are a helpful assistant. 请非常开心地说一句话。<|endofprompt|>"),
    ("F1伤心", "You are a helpful assistant. 请非常伤心地说一句话。<|endofprompt|>"),
    ("F1生气", "You are a helpful assistant. 请非常生气地说一句话。<|endofprompt|>"),
]:
    r = synth_hybrid(label, instr, drop_llm_token=False)
    if r:
        res.append(r)

print("=" * 64)
print("F2 混合模式：删除 llm_prompt_speech_token（对齐官方 instruct2 语义）")
for label, instr in [
    ("F2开心", "You are a helpful assistant. 请非常开心地说一句话。<|endofprompt|>"),
    ("F2伤心", "You are a helpful assistant. 请非常伤心地说一句话。<|endofprompt|>"),
]:
    r = synth_hybrid(label, instr, drop_llm_token=True)
    if r:
        res.append(r)

print("=" * 64)
print("汇总")
print(f"{'标签':<10}{'音频s':<9}{'耗时s':<9}{'RTF':<8}{'RMS':<9}{'ZCR':<9}{'包络std':<9}")
print("-" * 62)
for r in res:
    print(f"{r['label']:<10}{r['dur']:<9.2f}{r['el']:<9.2f}"
          f"{r['rtf']:<8.3f}{r['rms']:<9.4f}{r['zcr']:<9.4f}{r['env_std']:<9.4f}")

if torch.cuda.is_available():
    print(f"\n峰值显存 {torch.cuda.max_memory_allocated()/1024**3:.2f} GB")
