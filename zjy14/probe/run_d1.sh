#!/usr/bin/env bash
# 运行探针脚本，过滤掉上游噪声日志。
#
# 用法：run_d1.sh <python脚本路径> [传给 python 的额外参数...]
#
# 注意：过滤规则必须精确，早期版本用 `^    ` 会误杀
# 以空格缩进的表格数据行。现在只过滤明确的日志前缀。
set -uo pipefail

SCRIPT="${1:?用法: run_d1.sh <python脚本路径> [参数...]}"
shift || true

# shellcheck disable=SC1091
source "${HOME}/cosyvoice-venv/bin/activate"
cd "${HOME}/CosyVoice" || exit 1
export PYTHONPATH=/mnt/d/xiangmudaima/zjy14

# 关键：必须转发额外参数，否则 --minutes 30 之类的选项会被吞掉
# （曾因此把 30 分钟压测跑成了默认 5 分钟）
python "$SCRIPT" "$@" 2>&1 \
  | grep -vE \
    -e 'it/s|it\]' \
    -e 'UserWarning|warnings\.warn' \
    -e ' - (DEBUG|INFO|WARNING) - ' \
    -e '^(DEBUG|INFO|WARNING):' \
    -e 'modelscope|RemoteException|Downloading|Sliding Window|Authentication' \
    -e 'no frontend|torchaudio' \
    -e 'Thread-[0-9]' \
    -e '^Traceback|^  File "|^AssertionError|^RuntimeError' \
    -e 'yield speech len|synthesis text |input frame rate' \
    -e 'INFO 合成完成|WARNING 合成文本过短' \
  | grep -vE '^[[:space:]]*$'
exit "${PIPESTATUS[0]}"
