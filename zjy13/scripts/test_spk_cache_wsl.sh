#!/usr/bin/env bash
# 参考音频编码缓存的效果测试
#
# 背景：cosyvoice/cli/cosyvoice.py:69 提供 add_zero_shot_spk()，
# 可以预先计算并缓存参考音频的说话人嵌入与 speech token。
#
# 不缓存时，每次 inference_zero_shot 都要：
#   参考音频 -> campplus.onnx -> 192 维嵌入
#            -> speech_tokenizer_v3.onnx -> 参考 token
#
# 本脚本对比"每次重算"与"预先缓存"两种方式的耗时差异。
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

echo "=========================================="
echo "  参考音频缓存效果测试"
echo "=========================================="

python - <<PYEOF
import sys, time, statistics
sys.path.append('third_party/Matcha-TTS')

import torch
from cosyvoice.cli.cosyvoice import CosyVoice3

MODEL = '${MODEL_DIR}'
PROMPT_WAV = '${PROMPT_WAV}'
PROMPT_TEXT = 'You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。'
TEXT = '今天天气不错，我们下午去公园散步吧。'
N = 5

torch.cuda.empty_cache()
cosy = CosyVoice3(MODEL, fp16=False)

# 预热
for _ in range(2):
    list(cosy.inference_zero_shot('你好。', PROMPT_TEXT, PROMPT_WAV, stream=False))

def bench(label, fn, n=N):
    times = []
    for _ in range(n):
        t0 = time.time()
        chunks = list(fn())
        dt = time.time() - t0
        if chunks:
            dur = chunks[0]['tts_speech'].shape[-1] / cosy.sample_rate
            times.append((dt, dur))
    synths = [t[0] for t in times]
    durs = [t[1] for t in times]
    rtfs = [s/d for s, d in times]
    print(f'  {label:22} 合成 {statistics.fmean(synths):.3f}s  '
          f'RTF {statistics.fmean(rtfs):.3f}  音频 {statistics.fmean(durs):.2f}s')
    return statistics.fmean(synths), statistics.fmean(rtfs)

print()
print('【方式 A】每次重算参考音频编码')
a_synth, a_rtf = bench('recompute', lambda: cosy.inference_zero_shot(TEXT, PROMPT_TEXT, PROMPT_WAV, stream=False))

print()
print('【方式 B】预先缓存参考音频编码')
# add_zero_shot_spk 会把编码结果存进 frontend.spk2info
t0 = time.time()
cosy.add_zero_shot_spk(PROMPT_TEXT, PROMPT_WAV, 'xiaoman')
print(f'  缓存构建耗时: {time.time()-t0:.3f}s')
b_synth, b_rtf = bench('cached', lambda: cosy.inference_zero_shot(TEXT, '', PROMPT_WAV, zero_shot_spk_id='xiaoman', stream=False))

print()
print('=' * 50)
print('  对比')
print('=' * 50)
print(f'  重算  合成 {a_synth:.3f}s  RTF {a_rtf:.3f}')
print(f'  缓存  合成 {b_synth:.3f}s  RTF {b_rtf:.3f}')
delta = (a_synth - b_synth) / a_synth * 100
print(f'  节省  {a_synth - b_synth:+.3f}s ({delta:+.1f}%)')
PYEOF
