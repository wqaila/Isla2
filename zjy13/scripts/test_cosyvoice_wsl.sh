#!/usr/bin/env bash
# CosyVoice 3.0 最小合成验证（零样本克隆）
#
# 用法：
#   wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/test_cosyvoice_wsl.sh
#
# 产物：/mnt/d/xiangmudaima/zjy13/logs/tts/cosyvoice_test_*.wav
#      直接写到 Windows D 盘，方便播放验证
#
# 重要说明（实测）：
# CosyVoice3 没有预置说话人，list_available_spks() 返回空列表。
# 必须用 inference_zero_shot / inference_cross_lingual / inference_instruct2，
# 并提供一段参考音频做零样本克隆。
# 用 CosyVoice2 的 inference_sft('中文女') 会报 KeyError: '中文女'。
set -uo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

VENV_DIR="${HOME}/cosyvoice-venv"
COSYVOICE_DIR="${HOME}/CosyVoice"
MODEL_DIR="${HOME}/models/Fun-CosyVoice3-0.5B-2512"
PROMPT_WAV="${COSYVOICE_DIR}/asset/zero_shot_prompt.wav"
OUT_DIR="/mnt/d/xiangmudaima/zjy13/logs/tts"

[ -d "${VENV_DIR}" ] || fail "虚拟环境不存在: ${VENV_DIR}"
[ -d "${COSYVOICE_DIR}" ] || fail "仓库不存在: ${COSYVOICE_DIR}"
[ -d "${MODEL_DIR}" ] || fail "模型不存在: ${MODEL_DIR}"
[ -f "${PROMPT_WAV}" ] || fail "参考音频不存在: ${PROMPT_WAV}"

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
mkdir -p "${OUT_DIR}"
cd "${COSYVOICE_DIR}"

info "环境"
echo "  Python   : $(python --version 2>&1)"
echo "  模型     : ${MODEL_DIR}"
echo "  参考音频 : ${PROMPT_WAV}"
echo "  输出     : ${OUT_DIR}"
echo

info "GPU 状态"
python - <<'PYEOF'
import torch
print(f"  torch  : {torch.__version__}")
print(f"  CUDA   : {torch.cuda.is_available()}")
if torch.cuda.is_available():
    cap = torch.cuda.get_device_capability(0)
    archs = torch.cuda.get_arch_list()
    print(f"  GPU    : {torch.cuda.get_device_name(0)}")
    print(f"  sm     : sm_{cap[0]}{cap[1]} 匹配={'sm_%d%d' % cap in archs}")
PYEOF
echo

info "加载模型并合成（零样本克隆）"
python - <<PYEOF
import sys, time, os
sys.path.append('third_party/Matcha-TTS')

import torchaudio
from cosyvoice.cli.cosyvoice import CosyVoice3

t0 = time.time()
cosy = CosyVoice3('${MODEL_DIR}')
load_s = time.time() - t0
print(f'  模型加载 : {load_s:.1f}s')
print(f'  采样率   : {cosy.sample_rate}')

PROMPT_WAV = '${PROMPT_WAV}'
OUT_DIR = '${OUT_DIR}'

# CosyVoice3 的 prompt 文本格式：以 <|endofprompt|> 分隔指令与参考文本
# 官方示例用 "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。"
PROMPT_TEXT = 'You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。'

CASES = [
    ('zero_shot', '今天天气不错，我们下午去公园散步吧。'),
    ('zero_shot2', '你好，我是小满。有什么想聊的吗？'),
]

results = []
for name, text in CASES:
    t0 = time.time()
    chunks = list(cosy.inference_zero_shot(text, PROMPT_TEXT, PROMPT_WAV, stream=False))
    synth_s = time.time() - t0
    if not chunks:
        print(f'  [{name}] 未生成音频')
        continue
    speech = chunks[0]['tts_speech']
    dur = speech.shape[-1] / cosy.sample_rate
    out = os.path.join(OUT_DIR, f'cosyvoice_{name}.wav')
    torchaudio.save(out, speech, cosy.sample_rate)
    rtf = synth_s / dur if dur > 0 else 0
    print(f'  [{name}] "{text}"')
    print(f'        文本 {len(text)} 字 | 音频 {dur:.2f}s | 合成 {synth_s:.2f}s | RTF {rtf:.3f}')
    print(f'        -> {out}')
    results.append((name, dur, synth_s, rtf))

# 顺便测一下 instruct2（带风格指令）
try:
    t0 = time.time()
    chunks = list(cosy.inference_instruct2(
        '别急，慢慢来就好。',
        'You are a helpful assistant. 请用温柔、缓慢的语气说这句话。<|endofprompt|>',
        PROMPT_WAV, stream=False))
    synth_s = time.time() - t0
    if chunks:
        speech = chunks[0]['tts_speech']
        dur = speech.shape[-1] / cosy.sample_rate
        out = os.path.join(OUT_DIR, 'cosyvoice_instruct.wav')
        torchaudio.save(out, speech, cosy.sample_rate)
        print(f'  [instruct] "别急，慢慢来就好。"（温柔缓慢）')
        print(f'        音频 {dur:.2f}s | 合成 {synth_s:.2f}s | RTF {synth_s/dur:.3f}')
        print(f'        -> {out}')
except Exception as e:
    print(f'  [instruct] 跳过: {type(e).__name__}: {e}')

print()
print('  === 汇总 ===')
for name, dur, synth_s, rtf in results:
    print(f'  {name:12} 音频 {dur:5.2f}s  合成 {synth_s:5.2f}s  RTF {rtf:.3f}')
PYEOF

RC=$?
echo
if [ ${RC} -eq 0 ]; then
    info "================ 合成成功 ================"
    ls -lh "${OUT_DIR}"/cosyvoice_*.wav 2>/dev/null | sed 's/^/  /'
    echo
    echo "在 Windows 上播放："
    echo "  D:\\xiangmudaima\\zjy13\\logs\\tts\\cosyvoice_zero_shot.wav"
else
    fail "合成失败，返回码 ${RC}"
fi
