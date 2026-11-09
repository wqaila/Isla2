#!/usr/bin/env bash
set -uo pipefail
cd "${HOME}/CosyVoice"
echo "===== utils/common.py ====="
sed -n '1,80p' cosyvoice/utils/common.py
echo
echo "===== special tokens (tokenizer.py around 240-300) ====="
sed -n '235,300p' cosyvoice/tokenizer/tokenizer.py
echo
echo "===== grep emotion/情绪 in repo docs ====="
grep -rn -i 'emotion\|情绪' --include=*.py --include=*.md . 2>/dev/null | grep -v third_party | head -20
