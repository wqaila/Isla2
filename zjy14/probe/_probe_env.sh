#!/usr/bin/env bash
# Phase 0 环境探测：只读，不改任何文件
set -uo pipefail
source "${HOME}/cosyvoice-venv/bin/activate" 2>/dev/null || { echo "NO_VENV"; exit 1; }

echo "===== python ====="
python -V
echo "===== torch ====="
python - <<'PY'
import torch
print("torch", torch.__version__)
print("cuda_runtime", torch.version.cuda)
print("available", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu", torch.cuda.get_device_name(0))
    print("capability", torch.cuda.get_device_capability(0))
    print("arch_list", torch.cuda.get_arch_list())
    free, total = torch.cuda.mem_get_info()
    print("vram_free_MB", round(free / 1024**2), "vram_total_MB", round(total / 1024**2))
PY
echo "===== key packages ====="
pip list 2>/dev/null | grep -Ei '^(torch|torchaudio|onnxruntime|vllm|transformers|fastapi|uvicorn|numpy|modelscope|deepspeed|flash|gradio|hyperpyyaml|matcha)'
echo "===== nvidia-smi ====="
nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader
echo "===== os ====="
. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"
uname -r
nproc
free -g | head -2
echo "===== repo ====="
cd "${HOME}/CosyVoice" && git log -1 --format='%H %ad' --date=short
echo "===== cosyvoice3 api surface ====="
grep -nE '^\s+def ' "${HOME}/CosyVoice/cosyvoice/cli/cosyvoice.py" | sed -n '1,200p'
echo "===== model yaml sample_rate / tokenizer ====="
grep -nE 'sample_rate|token_hop_len|token_frame_rate|speech_tokenizer|onnx|fp16' "${HOME}/models/Fun-CosyVoice3-0.5B-2512/cosyvoice3.yaml" | head -40
