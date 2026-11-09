#!/usr/bin/env bash
# 在 WSL 中启动 zjy14 服务
#
# 用法：
#   wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/run_server.sh
#
# 环境变量：
#   ZJY14_PORT   覆盖监听端口（默认取 configs/gpu.yaml）
#   ZJY14_DATA_DIR  覆盖数据目录（默认 ~/zjy14-data）

set -uo pipefail

PROJECT_DIR="/mnt/d/xiangmudaima/zjy14"
VENV="${HOME}/cosyvoice-venv"

[ -d "${VENV}" ] || { echo "虚拟环境不存在: ${VENV}"; exit 1; }
# shellcheck disable=SC1091
source "${VENV}/bin/activate"

cd "${PROJECT_DIR}" || exit 1
export PYTHONPATH="${PROJECT_DIR}"

echo "=============================================="
echo "  zjy14 Custom Voice Engine"
echo "=============================================="
echo "  项目: ${PROJECT_DIR}"
echo "  数据: ${ZJY14_DATA_DIR:-${HOME}/zjy14-data}"
echo "  文档: http://127.0.0.1:${ZJY14_PORT:-50001}/docs"
echo "  健康: http://127.0.0.1:${ZJY14_PORT:-50001}/health"
echo "  Ctrl+C 停止"
echo "=============================================="
echo

exec python -m custom_voice.server
