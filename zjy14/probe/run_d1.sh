#!/usr/bin/env bash
# 运行 D1 相关探针，过滤掉噪声日志
set -uo pipefail
SCRIPT="${1:?用法: run_d1.sh <python脚本路径>}"
# shellcheck disable=SC1091
source "${HOME}/cosyvoice-venv/bin/activate"
cd "${HOME}/CosyVoice" || exit 1
export PYTHONPATH=/mnt/d/xiangmudaima/zjy14
python "$SCRIPT" 2>&1 \
  | grep -vE 'it/s|it\]|UserWarning|warnings\.warn|DEBUG|modelscope|RemoteException|Downloading|Sliding Window|Authentication|no frontend|torchaudio|Thread-[0-9]|^Traceback|^  File |^    |^AssertionError|^\s*$'
exit ${PIPESTATUS[0]}
