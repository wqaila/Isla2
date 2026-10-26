#!/usr/bin/env bash
# 验证 token_hop_len 跨调用污染
#
# 发现：cosyvoice/cli/model.py:360
#   self.token_hop_len = min(self.token_max_hop_len, self.token_hop_len * self.stream_scale_factor)
# token_hop_len 是模型实例的属性，出包后翻倍（25→50→100），且**不重置**。
# 这意味着：
#   1. 同一次合成内，后面每包要攒更多 token
#   2. 上一次合成结束后，下次合成首包也继承了放大的值
#
# 本脚本验证第 2 点：连续两次相同请求，第二次首包应该更慢（若污染存在）。
set -uo pipefail

VENV_DIR="${HOME}/cosyvoice-venv"
COSYVOICE_DIR="${HOME}/CosyVoice"
MODEL_DIR="${HOME}/models/Fun-CosyVoice3-0.5B-2512"
PROMPT_WAV="${COSYVOICE_DIR}/asset/zero_shot_prompt.wav"

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
cd "${COSYVOICE_DIR}"

python - <<PYEOF
import sys, time
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
print('=' * 62)
print('  token_hop_len 跨调用污染验证')
print('=' * 62)
print(f'  初始 token_hop_len = {model.token_hop_len}')
print(f'  上限 token_max_hop_len = {model.token_max_hop_len}')
print()

def run_once(tag):
    before = model.token_hop_len
    t0 = time.time()
    first = None
    n = 0
    for ch in cosy.inference_zero_shot(TEXT, '', PROMPT_WAV, zero_shot_spk_id='xiaoman', stream=True):
        if first is None:
            first = time.time() - t0
        n += 1
    after = model.token_hop_len
    print(f'  {tag}: 进入时 hop={before:3d} | 首包 {first:.2f}s | 包数 {n} | 退出时 hop={after:3d}')
    return first, before

for i in range(4):
    run_once(f'第{i+1}次')

print()
print('  结论：若"进入时 hop"逐次变大，说明状态被污染。')
print('  修复：每次合成前手动重置 model.token_hop_len = 25')
print()

# 验证修复效果
print('=' * 62)
print('  修复后（每次重置 token_hop_len = 25）')
print('=' * 62)
firsts = []
for i in range(3):
    model.token_hop_len = 25
    t0 = time.time()
    first = None
    n = 0
    for ch in cosy.inference_zero_shot(TEXT, '', PROMPT_WAV, zero_shot_spk_id='xiaoman', stream=True):
        if first is None:
            first = time.time() - t0
        n += 1
    firsts.append(first)
    print(f'  第{i+1}次: 首包 {first:.2f}s | 包数 {n}')

import statistics
print(f'\n  重置后首包均值: {statistics.fmean(firsts):.2f}s  (波动 {max(firsts)-min(firsts):.2f}s)')
PYEOF
