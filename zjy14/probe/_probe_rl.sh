#!/usr/bin/env bash
set -uo pipefail
cd "${HOME}/CosyVoice"
echo "===== 模型 README 中关于 rl / 版本的说明 ====="
grep -nE -i 'rl|reinforce|2512|version|更新|release' \
  "${HOME}/models/Fun-CosyVoice3-0.5B-2512/README.md" | head -40
echo
echo "===== 仓库里 grep llm.rl ====="
grep -rn 'llm.rl\|llm_rl\|\.rl\.pt' --include=*.py --include=*.md --include=*.yaml . 2>/dev/null | grep -v third_party | head -20
echo
echo "===== 模型目录文件时间与大小 ====="
ls -la --time-style=long-iso "${HOME}/models/Fun-CosyVoice3-0.5B-2512/" | grep -E 'llm|flow|hift'
echo
echo "===== cosyvoice3.yaml 里 llm 相关配置 ====="
sed -n '1,60p' "${HOME}/models/Fun-CosyVoice3-0.5B-2512/cosyvoice3.yaml"
