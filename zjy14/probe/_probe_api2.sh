#!/usr/bin/env bash
set -uo pipefail
cd "${HOME}/CosyVoice"
echo "===== CosyVoice2 class (lines 141-190) ====="
sed -n '141,190p' cosyvoice/cli/cosyvoice.py
echo
echo "===== frontend methods ====="
grep -nE '    def ' cosyvoice/cli/frontend.py
echo
echo "===== allowed_special in yaml ====="
grep -nA4 'allowed_special' "${HOME}/models/Fun-CosyVoice3-0.5B-2512/cosyvoice3.yaml"
echo
echo "===== instruct support? grep instruct in cli ====="
grep -rn 'instruct' cosyvoice/cli/cosyvoice.py | head -30
echo
echo "===== CosyVoice3Model __init__ + token2wav ====="
sed -n '397,470p' cosyvoice/cli/model.py
