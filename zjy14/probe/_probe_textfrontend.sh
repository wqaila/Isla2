#!/usr/bin/env bash
set -uo pipefail
cd "${HOME}/CosyVoice"
echo "===== frontend __init__ 全文 ====="
sed -n '32,78p' cosyvoice/cli/frontend.py
echo
echo "===== ttsfrd / wetext 相关 ====="
grep -rn 'ttsfrd\|wetext\|no frontend' --include=*.py cosyvoice/ | head -20
echo
echo "===== 检查 ttsfrd / wetext 是否已安装 ====="
source "${HOME}/cosyvoice-venv/bin/activate"
python - <<'PY'
for name in ("ttsfrd", "wetext", "WeTextProcessing", "tn"):
    try:
        __import__(name)
        print(f"{name:20} OK")
    except Exception as exc:
        print(f"{name:20} MISSING ({type(exc).__name__})")
PY
