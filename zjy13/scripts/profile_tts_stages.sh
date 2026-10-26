#!/usr/bin/env bash
# CosyVoice3 三段耗时剖析
#
# 目的：把合成时间拆成 LLM / Flow / HiFT 三段，找出真正的瓶颈。
# 方法：直接调用内部组件，分别计时（不经过 tts() 的线程编排）。
set -uo pipefail

VENV_DIR="${HOME}/cosyvoice-venv"
COSYVOICE_DIR="${HOME}/CosyVoice"
MODEL_DIR="${HOME}/models/Fun-CosyVoice3-0.5B-2512"
PROMPT_WAV="${COSYVOICE_DIR}/asset/zero_shot_prompt.wav"

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
cd "${COSYVOICE_DIR}"

python - <<PYEOF
import sys, time, statistics
sys.path.append('third_party/Matcha-TTS')

import torch
from cosyvoice.cli.cosyvoice import CosyVoice3

MODEL = '${MODEL_DIR}'
PROMPT_WAV = '${PROMPT_WAV}'
PROMPT_TEXT = 'You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。'
TEXT = '今天天气不错，我们下午去公园散步吧。'

torch.cuda.empty_cache()
cosy = CosyVoice3(MODEL, fp16=False)
cosy.add_zero_shot_spk(PROMPT_TEXT, PROMPT_WAV, 'xiaoman')
for _ in range(2):
    list(cosy.inference_zero_shot('你好。', '', PROMPT_WAV, zero_shot_spk_id='xiaoman', stream=False))

model = cosy.model
frontend = cosy.frontend

print('=' * 66)
print('  CosyVoice3 三段耗时剖析')
print('=' * 66)

# 准备输入
mi = frontend.frontend_zero_shot(TEXT, frontend.text_normalize(PROMPT_TEXT, split=False), PROMPT_WAV, cosy.sample_rate, 'xiaoman')

text_tok = mi['text'].to(model.device)
text_len = torch.tensor([text_tok.shape[1]], dtype=torch.int32).to(model.device)
ptext = mi['prompt_text'].to(model.device)
ptext_len = torch.tensor([ptext.shape[1]], dtype=torch.int32).to(model.device)
pspeech = mi['llm_prompt_speech_token'].to(model.device)
pspeech_len = torch.tensor([pspeech.shape[1]], dtype=torch.int32).to(model.device)
emb = mi['llm_embedding'].to(model.device)

# ---- 1. LLM ----
torch.cuda.synchronize()
t0 = time.time()
tokens = []
with torch.cuda.amp.autocast(False):
    for tok in model.llm.inference(
            text=text_tok, text_len=text_len,
            prompt_text=ptext, prompt_text_len=ptext_len,
            prompt_speech_token=pspeech, prompt_speech_token_len=pspeech_len,
            embedding=emb, uuid='bench'):
        tokens.append(tok)
torch.cuda.synchronize()
llm_s = time.time() - t0
print(f'\n  1. LLM（文字→语音token）')
print(f'     生成 {len(tokens)} token | 耗时 {llm_s:.3f}s | {llm_s*1000/len(tokens):.2f} ms/token')

# ---- 2. Flow ----
token_t = torch.tensor(tokens).unsqueeze(0).to(model.device, dtype=torch.int32)
fps = mi['flow_prompt_speech_token'].to(model.device)
fps_len = torch.tensor([fps.shape[1]], dtype=torch.int32).to(model.device)
pfeat = mi['prompt_speech_feat'].to(model.device)
pfeat_len = torch.tensor([pfeat.shape[1]], dtype=torch.int32).to(model.device)
femb = mi['flow_embedding'].to(model.device)

torch.cuda.synchronize()
t0 = time.time()
with torch.cuda.amp.autocast(False):
    mel, _ = model.flow.inference(
        token=token_t, token_len=torch.tensor([token_t.shape[1]], dtype=torch.int32).to(model.device),
        prompt_token=fps, prompt_token_len=fps_len,
        prompt_feat=pfeat, prompt_feat_len=pfeat_len,
        embedding=femb, streaming=False, finalize=True)
torch.cuda.synchronize()
flow_s = time.time() - t0
print(f'\n  2. Flow（token→mel频谱）')
print(f'     输出 {mel.shape[2]} 帧 mel | 耗时 {flow_s:.3f}s')

# ---- 3. HiFT ----
torch.cuda.synchronize()
t0 = time.time()
with torch.cuda.amp.autocast(False):
    speech, _ = model.hift.inference(speech_feat=mel, finalize=True)
torch.cuda.synchronize()
hift_s = time.time() - t0
dur = speech.shape[-1] / cosy.sample_rate
print(f'\n  3. HiFT（mel→波形）')
print(f'     输出 {dur:.2f}s 音频 | 耗时 {hift_s:.3f}s')

total = llm_s + flow_s + hift_s
print()
print('=' * 66)
print('  占比')
print('=' * 66)
for name, v in [('LLM', llm_s), ('Flow', flow_s), ('HiFT', hift_s)]:
    print(f'  {name:<8} {v:6.3f}s  {v/total*100:5.1f}%  {"#" * int(v/total*40)}')
print(f'  {"合计":<8} {total:6.3f}s')
print()
print(f'  实时倍速: {dur/total:.2f}x')
PYEOF
