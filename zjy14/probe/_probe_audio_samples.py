"""Phase 0 探针 4：导出可盲听的音频样本。

基于已验证可行的 F2 混合模式（缓存 speaker 条件 + 替换 prompt_text 为指令 + 删除 llm_prompt_speech_token），
导出 8 个样本到 /mnt/d/xiangmudaima/zjy14/_audio_probe/，供人工盲听判断情绪是否真的生效。

同时导出纯 zero_shot 的 [laughter] / [breath] 样本，验证非语言事件。
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402

MODEL = "/home/user/models/Fun-CosyVoice3-0.5B-2512"
REF_WAV = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav"
REF_TXT = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt"
PREFIX = "You are a helpful assistant.<|endofprompt|>"
OUT = Path("/mnt/d/xiangmudaima/zjy14/_audio_probe")
OUT.mkdir(parents=True, exist_ok=True)

from cosyvoice.cli.cosyvoice import CosyVoice3  # noqa: E402

ref_text = open(REF_TXT, encoding="utf-8").read().strip()
if "<|endofprompt|>" not in ref_text:
    ref_text = PREFIX + ref_text

cosy = CosyVoice3(MODEL, fp16=False)
cosy.add_zero_shot_spk(ref_text, REF_WAV, "elysia")
print("模型就绪")

TEXT = "今天天气真不错，我们一起出去走走吧。"
SR = cosy.sample_rate


def reset_hop():
    if hasattr(cosy.model, "token_hop_len"):
        cosy.model.token_hop_len = 25


def save(name, chunks):
    if not chunks:
        print(f"  {name}: 无输出")
        return
    x = chunks[0]["tts_speech"].detach().cpu().float().numpy().squeeze()
    p = OUT / f"{name}.wav"
    sf.write(str(p), x, SR, subtype="PCM_16")
    print(f"  {name}.wav  音频{len(x)/SR:.2f}s")


def hybrid(instruct_text):
    cached = dict(cosy.frontend.spk2info["elysia"])
    ttk, ttl = cosy.frontend._extract_text_token(TEXT)
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
    return list(cosy.model.tts(**mi, stream=False, speed=1.0))


print("导出基线")
reset_hop()
save("00_baseline", list(cosy.inference_zero_shot(
    TEXT, "", REF_WAV, zero_shot_spk_id="elysia", stream=False)))

print("导出情绪样本（F2 混合模式）")
EMO = [
    ("01_happy", "You are a helpful assistant. 请非常开心地说一句话。<|endofprompt|>"),
    ("02_sad", "You are a helpful assistant. 请非常伤心地说一句话。<|endofprompt|>"),
    ("03_angry", "You are a helpful assistant. 请非常生气地说一句话。<|endofprompt|>"),
    ("04_slow", "You are a helpful assistant. 请用尽可能慢地语速说一句话。<|endofprompt|>"),
    ("05_fast", "You are a helpful assistant. 请用尽可能快地语速说一句话。<|endofprompt|>"),
    ("06_soft", "You are a helpful assistant. Please say a sentence in a very soft voice.<|endofprompt|>"),
]
for name, instr in EMO:
    reset_hop()
    try:
        save(name, hybrid(instr))
    except Exception as exc:  # noqa: BLE001
        print(f"  {name}: 失败 {type(exc).__name__}: {exc}")

print("导出非语言事件（zero_shot + 标记）")
for name, txt in [
    ("07_laughter", TEXT + "[laughter]。"),
    ("08_breath", TEXT + "[breath]。"),
    ("09_sigh", TEXT + "[sigh]。"),
]:
    reset_hop()
    try:
        save(name, list(cosy.inference_zero_shot(
            txt, "", REF_WAV, zero_shot_spk_id="elysia", stream=False)))
    except Exception as exc:  # noqa: BLE001
        print(f"  {name}: 失败 {type(exc).__name__}: {exc}")

print(f"\n输出目录: {OUT}")
print(f"峰值显存 {torch.cuda.max_memory_allocated()/1024**3:.2f} GB")
