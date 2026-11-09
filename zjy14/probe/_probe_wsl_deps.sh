#!/usr/bin/env bash
set -uo pipefail
source "${HOME}/cosyvoice-venv/bin/activate"
echo "===== 关键包 ====="
pip list 2>/dev/null | grep -Ei '^(fastapi|uvicorn|pydantic|pytest|httpx|numpy|soundfile|PyYAML|python-dotenv|starlette|anyio|aiofiles|websockets)'
echo
echo "===== python 路径 ====="
which python
echo
echo "===== venv 目录 ====="
ls -d ~/cosyvoice-venv
echo
echo "===== 磁盘（WSL 侧）====="
df -h / /mnt/d 2>/dev/null | head -4
