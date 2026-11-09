#!/usr/bin/env bash
set -uo pipefail
cd "${HOME}/CosyVoice"
echo "===== frontend_zero_shot / instruct2 / text_normalize ====="
sed -n '127,215p' cosyvoice/cli/frontend.py
