#!/usr/bin/env bash
set -uo pipefail
ASR_VENV="/mnt/d/xiangmudaima/zjy13/.venv-asr"
if [ ! -d "$ASR_VENV" ]; then
  echo "ASR venv 不存在: $ASR_VENV"
  exit 1
fi
if [ ! -x "$ASR_VENV/bin/python" ]; then
  echo "python 不存在"
  exit 1
fi
"$ASR_VENV/bin/python" - <<'PY'
import sys
print("python", sys.version.split()[0])
for name in ("funasr", "torch", "numpy", "soundfile", "modelscope"):
    try:
        m = __import__(name)
        print(f"{name:12} OK  {getattr(m, '__version__', '?')}")
    except Exception as exc:
        print(f"{name:12} MISSING  {type(exc).__name__}")
PY
