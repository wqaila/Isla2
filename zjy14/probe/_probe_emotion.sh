#!/usr/bin/env bash
set -uo pipefail
cd "${HOME}/CosyVoice"
echo "===== tokenizer.py 120-235 ====="
sed -n '120,235p' cosyvoice/tokenizer/tokenizer.py
