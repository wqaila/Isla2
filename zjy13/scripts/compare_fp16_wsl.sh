#!/usr/bin/env bash
# CosyVoice3 fp16 vs fp32 性能对比
#
# 目的：确认 fp16 自动混合精度能否降低 RTF，以及音质是否受影响。
# 方法：同一进程内分别加载 fp32 和 fp16 模型，各预热一次后测 3 次。
#
# 说明：fp16 走 torch.cuda.amp.autocast，是运行时精度切换，
# 不改变权重存储，因此显存占用基本不变，但能加速矩阵运算。
set -uo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

VENV_DIR="${HOME}/cosyvoice-venv"
COSYVOICE_DIR="${HOME}/CosyVoice"
MODEL_DIR="${HOME}/models/Fun-CosyVoice3-0.5B-2512"
PROMPT_WAV="${COSYVOICE_DIR}/asset/zero_shot_prompt.wav"
OUT_DIR="/mnt/d/xiangmudaima/zjy13/logs/tts"

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
mkdir -p "${OUT_DIR}"
cd "${COSYVOICE_DIR}"

info "fp16 vs fp32 对比测试"

python - <<PYEOF
import sys, time, statistics, gc
sys.path.append('third_party/Matcha-TTS')

import torch
import torchaudio
from cosyvoice.cli.cosyvoice import CosyVoice3

MODEL = '${MODEL_DIR}'
PROMPT_WAV = '${PROMPT_WAV}'
PROMPT_TEXT = 'You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。'
TEXT = '今天天气不错，我们下午去公园散步吧。'
OUT_DIR = '${OUT_DIR}'

def run(precision: str, n: int = 3):
    """加载指定精度的模型，预热后测 n 次。"""
    fp16 = precision == 'fp16'
    print(f'\n{"="*52}')
    print(f'  {precision.upper()}')
    print(f'{"="*52}')

    torch.cuda.empty_cache()
    gc.collect()

    t0 = time.time()
    cosy = CosyVoice3(MODEL, fp16=fp16)
    load_s = time.time() - t0
    print(f'  加载      : {load_s:.1f}s')

    # 预热（不计入统计）
    t0 = time.time()
    list(cosy.inference_zero_shot('你好。', PROMPT_TEXT, PROMPT_WAV, stream=False))
    print(f'  预热      : {time.time()-t0:.2f}s')

    # 显存
    if torch.cuda.is_available():
        alloc = torch.cuda.memory_allocated() / 1e9
        peak = torch.cuda.max_memory_allocated() / 1e9
        print(f'  显存      : 已分配 {alloc:.2f} GB / 峰值 {peak:.2f} GB')

    rtfs, synth_times, durations = [], [], []
    for i in range(n):
        t0 = time.time()
        chunks = list(cosy.inference_zero_shot(TEXT, PROMPT_TEXT, PROMPT_WAV, stream=False))
        st = time.time() - t0
        if not chunks:
            continue
        dur = chunks[0]['tts_speech'].shape[-1] / cosy.sample_rate
        rtf = st / dur
        rtfs.append(rtf); synth_times.append(st); durations.append(dur)
        print(f'  第{i+1}次     : 音频 {dur:.2f}s | 合成 {st:.2f}s | RTF {rtf:.3f}')

    # 保存一份样本供听感对比
    out = f'{OUT_DIR}/compare_{precision}.wav'
    torchaudio.save(out, chunks[0]['tts_speech'], cosy.sample_rate)
    print(f'  样本      : {out}')

    result = {
        'precision': precision,
        'load_s': load_s,
        'rtf_mean': statistics.fmean(rtfs),
        'rtf_min': min(rtfs),
        'rtf_max': max(rtfs),
        'synth_mean': statistics.fmean(synth_times),
        'dur_mean': statistics.fmean(durations),
        'peak_gb': peak if torch.cuda.is_available() else 0,
    }
    del cosy
    torch.cuda.empty_cache()
    gc.collect()
    return result

results = []
for p in ['fp32', 'fp16']:
    try:
        results.append(run(p))
    except Exception as e:
        print(f'  {p} 失败: {type(e).__name__}: {e}')

print()
print('=' * 60)
print('  汇总对比')
print('=' * 60)
print(f'{"精度":<8}{"加载(s)":<10}{"RTF均值":<10}{"RTF最小":<10}{"峰值显存(GB)":<14}')
print('-' * 60)
for r in results:
    print(f'{r["precision"]:<8}{r["load_s"]:<10.1f}{r["rtf_mean"]:<10.3f}'
          f'{r["rtf_min"]:<10.3f}{r["peak_gb"]:<14.2f}')

if len(results) == 2:
    a, b = results
    speedup = (a['rtf_mean'] - b['rtf_mean']) / a['rtf_mean'] * 100
    print()
    if speedup > 0:
        print(f'  fp16 相对 fp32 提速: {speedup:.1f}%')
        print(f'  实时倍速: {1/a["rtf_mean"]:.2f}x -> {1/b["rtf_mean"]:.2f}x')
    else:
        print(f'  fp16 反而慢 {-speedup:.1f}%，建议用 fp32')
PYEOF
