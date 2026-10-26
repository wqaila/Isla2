#!/usr/bin/env bash
# 启动 CosyVoice3 TTS HTTP 服务（在 WSL2 内运行）
#
# 用法：
#   wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/serve_tts_wsl.sh
#
# 只监听 127.0.0.1:50000，Windows 侧通过 localhost 访问（WSL2 自动转发）。
set -uo pipefail

VENV_DIR="${HOME}/cosyvoice-venv"
SCRIPT="/mnt/d/xiangmudaima/zjy13/scripts/tts_server_wsl.py"
PORT="${TTS_PORT:-50000}"

[ -d "${VENV_DIR}" ] || { echo "虚拟环境不存在: ${VENV_DIR}"; exit 1; }
[ -f "${SCRIPT}" ] || { echo "服务脚本不存在: ${SCRIPT}"; exit 1; }

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

echo "=============================================="
echo "  CosyVoice3 TTS 服务"
echo "=============================================="
echo "  地址: http://127.0.0.1:${PORT}"
echo "  文档: http://127.0.0.1:${PORT}/docs"
echo "  健康: http://127.0.0.1:${PORT}/health"
echo
echo "  首次启动会加载模型并预热，约需 15-20 秒"
echo "  Ctrl+C 停止"
echo "=============================================="
echo

export TTS_PORT="${PORT}"
exec python "${SCRIPT}"
