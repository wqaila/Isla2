#!/usr/bin/env bash
set -uo pipefail
cd "${HOME}/CosyVoice"
echo "===== CosyVoice3 class (from line 185) ====="
sed -n '185,300p' cosyvoice/cli/cosyvoice.py
echo "===== model.py CosyVoice3Model signatures ====="
grep -nE 'class |def ' cosyvoice/cli/model.py | sed -n '1,200p'
