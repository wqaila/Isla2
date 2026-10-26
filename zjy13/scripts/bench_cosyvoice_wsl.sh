#!/usr/bin/env bash
# CosyVoice 3.0 性能基线测试
#
# 目的：确认首句慢是"冷启动"还是"稳定性能"，并测出真实 RTF 分布。
# 方法：同一段文本连续合成 5 次，观察 RTF 变化。
set -uo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $*"; }

VENV_DIR="${HOME}/cosyvoice-venv"
COSYVOICE_DIR="${HOME}/CosyVoice"
MODEL_DIR="${HOME}/models/Fun-CosyVoice3-0.5B-2512"
PROMPT_WAV="${COSYVOICE_DIR}/asset/zero_shot_prompt.wav"

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
cd "${COSYVOICE_DIR}"

info "CosyVoice3 性能基线（预热 + 5 次重复）"

python - <<PYEOF
import sys, time
sys.path.append('third_party/Matcha-TTS')

from cosyvoice.cli.cosyvoice import CosyVoice3

MODEL = '${MODEL_DIR}'
PROMPT_WAV = '${PROMPT_WAV}'
PROMPT_TEXT = 'You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。'
TEXT = '今天天气不错，我们下午去公园散步吧。'

t0 = time.time()
cosy = CosyVoice3(MODEL)
print(f'模型加载: {time.time()-t0:.1f}s  (采样率 {cosy.sample_rate})')

# 预热：不记录这一次，目的是让 CUDA kernel / 缓存就绪
t0 = time.time()
list(cosy.inference_zero_shot('你好。', PROMPT_TEXT, PROMPT_WAV, stream=False))
warm = time.time() - t0
print(f'预热: {warm:.2f}s\n')

print(f'{"轮次":<6}{"音频(s)":<10}{"合成(s)":<10}{"RTF":<8}{"首包(s)":<10}')
print('-' * 46)

rtfs = []
for i in range(5):
    t0 = time.time()
    chunks = list(cosy.inference_zero_shot(TEXT, PROMPT_TEXT, PROMPT_WAV, stream=False))
    synth = time.time() - t0
    if not chunks:
        continue
    dur = chunks[0]['tts_speech'].shape[-1] / cosy.sample_rate
    rtf = synth / dur
    rtfs.append(rtf)
    print(f'{i+1:<6}{dur:<10.2f}{synth:<10.2f}{rtf:<8.3f}{synth:<10.2f}')

if rtfs:
    import statistics
    s = sorted(rtfs)
    p95 = s[min(len(s)-1, int(round(0.95*(len(s)-1))))]
    print()
    print(f'RTF  均值 {statistics.fmean(rtfs):.3f}  最小 {min(rtfs):.3f}  最大 {max(rtfs):.3f}  P95 {p95:.3f}')
    print(f'实时倍速: {1/statistics.fmean(rtfs):.2f}x')
PYEOF
