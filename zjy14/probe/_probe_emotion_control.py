"""Phase 0 可行性探针：验证 CosyVoice3 的情绪/指令控制是否真的生效。

只读探测，不修改任何上游文件。输出到 stdout。
目的：为 zjy14 落地方案提供实测证据，避免把"请求被接受"当成"情绪真的表达了"。
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

print("=" * 60)
print("加载模型...")
t0 = time.time()
cosy = CosyVoice3(MODEL, fp16=False)
print(f"加载 {time.time()-t0:.1f}s  采样率 {cosy.sample_rate}")

# 注册角色音色（缓存参考音频编码）
cosy.add_zero_shot_spk(ref_text, REF_WAV, "elysia")
print("音色已注册")

TEXT = "今天天气真不错，我们一起出去走走吧。"


def stats(speech: torch.Tensor) -> dict:
    """从波形提取可用于对比的客观指标。"""
    x = speech.detach().cpu().float().numpy().squeeze()
    dur = len(x) / cosy.sample_rate
    rms = float(np.sqrt(np.mean(x**2)))
    peak = float(np.max(np.abs(x)))
    # 基频粗估：过零率作为语速/音高的粗略代理
    zcr = float(np.mean(np.abs(np.diff(np.sign(x))) > 0))
    return {"dur": dur, "rms": rms, "peak": peak, "zcr": zcr}


def run(label: str, fn) -> dict | None:
    t0 = time.time()
    try:
        chunks = list(fn())
    except Exception as exc:  # noqa: BLE001
        print(f"  [{label}] 失败: {type(exc).__name__}: {exc}")
        return None
    elapsed = time.time() - t0
    if not chunks:
        print(f"  [{label}] 无输出")
        return None
    speech = chunks[0]["tts_speech"]
    s = stats(speech)
    rtf = elapsed / s["dur"] if s["dur"] > 0 else 0
    print(f"  [{label}] 音频{s['dur']:.2f}s 耗时{elapsed:.2f}s RTF{rtf:.3f} "
          f"RMS{s['rms']:.4f} 峰值{s['peak']:.3f} ZCR{s['zcr']:.4f}")
    return {**s, "elapsed": elapsed, "rtf": rtf, "label": label}


def reset_hop():
    """修复上游 token_hop_len 跨调用污染。"""
    if hasattr(cosy.model, "token_hop_len"):
        cosy.model.token_hop_len = 25


print("=" * 60)
print("预热...")
reset_hop()
run("warmup", lambda: cosy.inference_zero_shot(
    "你好。", "", REF_WAV, zero_shot_spk_id="elysia", stream=False))

print("=" * 60)
print("实验 A：基线（无情绪指令）")
results = []
for i in range(2):
    reset_hop()
    r = run(f"A基线{i+1}", lambda: cosy.inference_zero_shot(
        TEXT, "", REF_WAV, zero_shot_spk_id="elysia", stream=False))
    if r:
        results.append(r)

print("=" * 60)
print("实验 B：instruct2 自然语言情绪指令（官方支持路径）")
INSTRUCTS = [
    ("B开心", "You are a helpful assistant. 请非常开心地说一句话。<|endofprompt|>"),
    ("B伤心", "You are a helpful assistant. 请非常伤心地说一句话。<|endofprompt|>"),
    ("B生气", "You are a helpful assistant. 请非常生气地说一句话。<|endofprompt|>"),
    ("B慢速", "You are a helpful assistant. 请用尽可能慢地语速说一句话。<|endofprompt|>"),
    ("B快速", "You are a helpful assistant. 请用尽可能快地语速说一句话。<|endofprompt|>"),
]
for label, instr in INSTRUCTS:
    reset_hop()
    r = run(label, lambda instr=instr: cosy.inference_instruct2(
        TEXT, instr, REF_WAV, zero_shot_spk_id="elysia", stream=False))
    if r:
        results.append(r)

print("=" * 60)
print("实验 C：文本内嵌 <|HAPPY|> token（假设验证，未在官方 TTS 示例中出现）")
for tag in ["<|HAPPY|>", "<|SAD|>"]:
    reset_hop()
    r = run(f"C{tag}", lambda tag=tag: cosy.inference_zero_shot(
        tag + TEXT, "", REF_WAV, zero_shot_spk_id="elysia", stream=False))
    if r:
        results.append(r)

print("=" * 60)
print("实验 D：非语言事件标记（[laughter] / [breath]）")
for tag in ["[laughter]", "[breath]"]:
    reset_hop()
    r = run(f"D{tag}", lambda tag=tag: cosy.inference_zero_shot(
        TEXT + tag, "", REF_WAV, zero_shot_spk_id="elysia", stream=False))
    if r:
        results.append(r)

print("=" * 60)
print("汇总（客观指标，仅用于筛选，最终需人工盲听）")
print(f"{'标签':<12}{'音频s':<9}{'耗时s':<9}{'RTF':<8}{'RMS':<9}{'ZCR':<9}")
print("-" * 56)
for r in results:
    print(f"{r['label']:<12}{r['dur']:<9.2f}{r['elapsed']:<9.2f}"
          f"{r['rtf']:<8.3f}{r['rms']:<9.4f}{r['zcr']:<9.4f}")

# 显存
if torch.cuda.is_available():
    print(f"\n峰值显存 {torch.cuda.max_memory_allocated()/1024**3:.2f} GB")
