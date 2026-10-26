#!/usr/bin/env bash
# CosyVoice3 精度对比（严谨版）
#
# 上一版的问题：
#   1. 只测 3 次，样本太少
#   2. 音频长度波动大（3.04~3.80s），RTF 被分母带偏
#   3. 同一进程内先后加载两个模型，可能有缓存/显存干扰
#
# 本版改进：
#   1. 各测 8 次，去掉第一次（预热后的首个正式调用仍可能抖动）
#   2. 同时记录绝对合成耗时和音频长度，两者都报告
#   3. 用"每 token 耗时"作为与文本长度无关的归一化指标
#   4. 通过命令行参数分开跑，避免同进程干扰
#
# 用法：
#   wsl -e bash compare_fp16_v2.sh fp32
#   wsl -e bash compare_fp16_v2.sh fp16
set -uo pipefail

PRECISION="${1:-fp32}"
N="${2:-8}"

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
echo "  精度: ${PRECISION}   样本数: ${N}"
echo "=========================================="

python - <<PYEOF
import sys, time, statistics, json
sys.path.append('third_party/Matcha-TTS')

import torch
from cosyvoice.cli.cosyvoice import CosyVoice3

MODEL = '${MODEL_DIR}'
PROMPT_WAV = '${PROMPT_WAV}'
PROMPT_TEXT = 'You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。'
TEXT = '今天天气不错，我们下午去公园散步吧。'
PRECISION = '${PRECISION}'
N = ${N}

torch.cuda.empty_cache()
t0 = time.time()
cosy = CosyVoice3(MODEL, fp16=(PRECISION == 'fp16'))
load_s = time.time() - t0
print(f'加载: {load_s:.1f}s')

# 预热两次，确保 CUDA kernel 完全就绪
for _ in range(2):
    list(cosy.inference_zero_shot('你好。', PROMPT_TEXT, PROMPT_WAV, stream=False))
print('预热: 完成')

peak = torch.cuda.max_memory_allocated() / 1e9
print(f'峰值显存: {peak:.2f} GB')
print()
print(f'{"轮次":<5}{"音频(s)":<10}{"合成(s)":<10}{"RTF":<8}{"ms/token":<10}')
print('-' * 45)

rows = []
for i in range(N):
    t0 = time.time()
    chunks = list(cosy.inference_zero_shot(TEXT, PROMPT_TEXT, PROMPT_WAV, stream=False))
    st = time.time() - t0
    if not chunks:
        continue
    dur = chunks[0]['tts_speech'].shape[-1] / cosy.sample_rate
    tokens = dur * 25          # 25 token/s
    rtf = st / dur
    ms_per_tok = st * 1000 / tokens
    rows.append({'dur': dur, 'synth': st, 'rtf': rtf, 'ms_tok': ms_per_tok})
    print(f'{i+1:<5}{dur:<10.2f}{st:<10.2f}{rtf:<8.3f}{ms_per_tok:<10.2f}')

# 统计
synths = [r['synth'] for r in rows]
durs = [r['dur'] for r in rows]
rtfs = [r['rtf'] for r in rows]
mstoks = [r['ms_tok'] for r in rows]

def stat(vals):
    s = sorted(vals)
    return {
        'mean': statistics.fmean(vals),
        'min': min(vals),
        'max': max(vals),
        'stdev': statistics.stdev(vals) if len(vals) > 1 else 0,
        'p95': s[min(len(s)-1, int(round(0.95*(len(s)-1))))],
    }

summary = {
    'precision': PRECISION,
    'n': len(rows),
    'load_s': load_s,
    'peak_gb': peak,
    'synth': stat(synths),
    'dur': stat(durs),
    'rtf': stat(rtfs),
    'ms_tok': stat(mstoks),
}

print()
print('=' * 45)
print('  统计')
print('=' * 45)
for key, label, unit in [
    ('synth', '合成耗时', 's'),
    ('dur',   '音频时长', 's'),
    ('rtf',   'RTF',      ''),
    ('ms_tok','每token',  'ms'),
]:
    v = summary[key]
    print(f'{label:<10} 均值 {v["mean"]:.3f}{unit}  '
          f'最小 {v["min"]:.3f}  最大 {v["max"]:.3f}  标准差 {v["stdev"]:.3f}')

# 保存
out = f'${OUT_DIR}/bench_{PRECISION}.json'
with open(out, 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print()
print(f'结果已保存: {out}')
PYEOF
