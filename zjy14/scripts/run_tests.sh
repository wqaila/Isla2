#!/usr/bin/env bash
# 在 WSL 中运行 zjy14 测试
set -uo pipefail

PROJECT_DIR="/mnt/d/xiangmudaima/zjy14"
VENV="${HOME}/cosyvoice-venv"

[ -d "${VENV}" ] || { echo "虚拟环境不存在: ${VENV}"; exit 1; }
# shellcheck disable=SC1091
source "${VENV}/bin/activate"

cd "${PROJECT_DIR}" || exit 1
export PYTHONPATH="${PROJECT_DIR}"
exec python -m pytest "$@"
