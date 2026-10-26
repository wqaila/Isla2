#!/usr/bin/env bash
# CosyVoice3 流式合成首包延迟测试
#
# 目的：量化流式相对非流式能缩短多少"用户等待时间"。
#
# 原理（来自 cosyvoice/cli/model.py:343-374）：
#   流式时，LLM 一边生成 token，主循环一边检查是否攒够
#   token_hop_len(25) + pre_lookahead_len(3) = 28 个 token，
#   够一包就立刻做 flow+hift 并 yield 出去。
#
# 本脚本记录：
#   - 第 1 包到达时间（用户等待时间）
#   - 各包间隔
#   - 总耗时
#   - 总音频时长
set -uo pipefail

VENV_DIR="${HOME}/cosyvoice-venv"
COSYVOICE_DIR="${HOME}/CosyVoice"
MODEL_DIR="${HOME}/models/Fun-CosyVoice3-0.5B-2512"
PROMPT_WAV="${COSYVOICE_DIR}/asset/zero_shot_prompt.wav"
OUT_DIR="/mnt/d/xiangmudaima/zjy13/logs/tts"

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
mkdir -p "${OUT_DIR}"
cd "${COSYVOICE_DIR}"

echo "=============================================="
echo "  流式合成首包延迟测试"
echo "=============================================="

python - <<PYEOF
import sys, time, statistics
sys.path.append('third_party/Matcha-TTS')

import torch
import torchaudio
from cosyvoice.cli.cosyvoice import CosyVoice3

MODEL = '${MODEL_DIR}'
PROMPT_WAV = '${PROMPT_WAV}'
PROMPT_TEXT = 'You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。'
OUT_DIR = '${OUT_DIR}'

TEXTS = [
    ('短句', '你好呀。'),
    ('中句', '今天天气不错，我们下午去公园散步吧。'),
    ('长句', '我最近在调试一块 ESP32-S3 的开发板，屏幕驱动那边一直有点问题，你有空的话能不能帮我看看？'),
]

torch.cuda.empty_cache()
cosy = CosyVoice3(MODEL, fp16=False)

# 缓存参考音频编码（上一轮实测能省 16%）
cosy.add_zero_shot_spk(PROMPT_TEXT, PROMPT_WAV, 'xiaoman')

# 预热
for _ in range(2):
    list(cosy.inference_zero_shot('你好。', '', PROMPT_WAV, zero_shot_spk_id='xiaoman', stream=False))
print('预热完成')
print()

print(f'{"文本":<8}{"字数":<6}{"首包(s)":<10}{"总耗时(s)":<12}{"音频(s)":<10}{"包数":<6}{"最大间隔(s)":<12}')
print('-' * 68)

summary = []
for label, text in TEXTS:
    t0 = time.time()
    first_at = None
    chunk_times = []
    total_audio = 0.0
    n = 0
    for chunk in cosy.inference_zero_shot(text, '', PROMPT_WAV,
                                          zero_shot_spk_id='xiaoman', stream=True):
        now = time.time() - t0
        if first_at is None:
            first_at = now
        chunk_times.append(now)
        n += 1
        total_audio += chunk['tts_speech'].shape[-1] / cosy.sample_rate

    total = time.time() - t0
    gaps = [chunk_times[i] - chunk_times[i-1] for i in range(1, len(chunk_times))]
    max_gap = max(gaps) if gaps else 0

    print(f'{label:<8}{len(text):<6}{first_at:<10.2f}{total:<12.2f}'
          f'{total_audio:<10.2f}{n:<6}{max_gap:<12.2f}')
    summary.append({
        'label': label, 'chars': len(text), 'first': first_at,
        'total': total, 'audio': total_audio, 'chunks': n, 'max_gap': max_gap,
    })

print()
print('=' * 68)
print('  对比：流式 vs 非流式（用户等待时间）')
print('=' * 68)
print(f'{"文本":<8}{"流式首包":<12}{"非流式":<12}{"改善":<12}')
print('-' * 50)

# 非流式耗时按 RTF 0.629 估算（缓存后的实测值）
RTF = 0.629
for s in summary:
    nonstream = s['audio'] * RTF
    improve = (nonstream - s['first']) / nonstream * 100
    print(f'{s["label"]:<8}{s["first"]:<12.2f}{nonstream:<12.2f}{improve:+.0f}%')

print()
print('说明：非流式耗时为按 RTF=0.629 折算的估算值，流式为首包实测。')
PYEOF
