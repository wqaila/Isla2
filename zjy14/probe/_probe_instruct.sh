#!/usr/bin/env bash
set -uo pipefail
cd "${HOME}/CosyVoice"
echo "===== example.py (repo) ====="
sed -n '1,80p' example.py 2>/dev/null || echo "no example.py"
echo
echo "===== grep instruct/emotion in example/webui ====="
grep -rn 'inference_instruct2\|instruct_text\|endofprompt' example.py webui.py 2>/dev/null | head -30
echo
echo "===== model README instruct section ====="
grep -nA6 -i 'instruct\|emotion\|情绪\|指令' "${HOME}/models/Fun-CosyVoice3-0.5B-2512/README.md" | head -60
