"""Phase 0 探针 2：验证 instruct2 的正确调用方式。

核心假设：当 zero_shot_spk_id 非空时，frontend_zero_shot 直接返回 spk2info 缓存，
          完全忽略 prompt_text 参数（即 instruct_text），导致指令失效。
          证据：实验 B 的 5 个不同指令产出几乎相同的超长音频（12.4~13.8s）。

本探针验证：
  E1. instruct2 + zero_shot_spk_id=''（走 prompt_text 路径，指令应生效）
  E2. instruct2 + zero_shot_spk_id='elysia'（走缓存，指令应失效）
  E3. zero_shot + 缓存（基线对照）
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
t0 = time.time()
cosy = CosyVoice3(MODEL, fp16=False)
print(f"加载 {time.time()-t0:.1f}s  采样率 {cosy.sample_rate}")
cosy.add_zero_shot_spk(ref_text, REF_WAV, "elysia")

TEXT = "今天天气真不错，我们一起出去走走吧。"
INSTR = "You are a helpful assistant. 请非常开心地说一句话。<|endofprompt|>"


def reset_hop():
    if hasattr(cosy.model, "token_hop_len"):
        cosy.model.token_hop_len = 25


def run(label, fn):
    reset_hop()
    t0 = time.time()
    try:
        chunks = list(fn())
    except Exception as exc:  # noqa: BLE001
        print(f"  [{label}] 失败: {type(exc).__name__}: {exc}")
        return None
    el = time.time() - t0
    if not chunks:
        print(f"  [{label}] 无输出")
        return None
    x = chunks[0]["tts_speech"].detach().cpu().float().numpy().squeeze()
    dur = len(x) / cosy.sample_rate
    rms = float(np.sqrt(np.mean(x**2)))
    zcr = float(np.mean(np.abs(np.diff(np.sign(x))) > 0))
    # 逐秒 RMS 包络的方差：情绪起伏越大，包络变化越剧烈
    seg = int(cosy.sample_rate * 0.5)
    env = [float(np.sqrt(np.mean(x[i:i+seg]**2))) for i in range(0, max(1, len(x)-seg), seg)]
    env_std = float(np.std(env)) if env else 0.0
    rtf = el / dur if dur else 0
    print(f"  [{label}] 音频{dur:.2f}s 耗时{el:.2f}s RTF{rtf:.3f} "
          f"RMS{rms:.4f} ZCR{zcr:.4f} 包络std{env_std:.4f}")
    return {"label": label, "dur": dur, "el": el, "rtf": rtf,
            "rms": rms, "zcr": zcr, "env_std": env_std}


print("=" * 64)
print("预热")
run("warmup", lambda: cosy.inference_zero_shot(
    "你好。", "", REF_WAV, zero_shot_spk_id="elysia", stream=False))

print("=" * 64)
print("E3 基线：zero_shot + 缓存 spk_id")
res = []
for i in range(2):
    r = run(f"E3基线{i+1}", lambda: cosy.inference_zero_shot(
        TEXT, "", REF_WAV, zero_shot_spk_id="elysia", stream=False))
    if r:
        res.append(r)

print("=" * 64)
print("E2 instruct2 + 缓存 spk_id（zjy13 当前做法，指令预期失效）")
for label, instr in [("E2开心", INSTR),
                     ("E2伤心", "You are a helpful assistant. 请非常伤心地说一句话。<|endofprompt|>")]:
    r = run(label, lambda instr=instr: cosy.inference_instruct2(
        TEXT, instr, REF_WAV, zero_shot_spk_id="elysia", stream=False))
    if r:
        res.append(r)

print("=" * 64)
print("E1 instruct2 + 空 spk_id（走 prompt_text，指令预期生效，但需重编码参考音频）")
for label, instr in [("E1开心", INSTR),
                     ("E1伤心", "You are a helpful assistant. 请非常伤心地说一句话。<|endofprompt|>"),
                     ("E1生气", "You are a helpful assistant. 请非常生气地说一句话。<|endofprompt|>")]:
    r = run(label, lambda instr=instr: cosy.inference_instruct2(
        TEXT, instr, REF_WAV, zero_shot_spk_id="", stream=False))
    if r:
        res.append(r)

print("=" * 64)
print("结论汇总")
print(f"{'标签':<12}{'音频s':<9}{'耗时s':<9}{'RTF':<8}{'RMS':<9}{'ZCR':<9}{'包络std':<9}")
print("-" * 64)
for r in res:
    print(f"{r['label']:<12}{r['dur']:<9.2f}{r['el']:<9.2f}"
          f"{r['rtf']:<8.3f}{r['rms']:<9.4f}{r['zcr']:<9.4f}{r['env_std']:<9.4f}")

if torch.cuda.is_available():
    print(f"\n峰值显存 {torch.cuda.max_memory_allocated()/1024**3:.2f} GB")
