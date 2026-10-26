#!/usr/bin/env bash
# CosyVoice 依赖修复脚本
#
# 背景：首次安装时步骤 6 失败，原因是 openai-whisper 从源码构建时报
#   ModuleNotFoundError: No module named 'pkg_resources'
# setuptools 80+ 移除了 pkg_resources，而 whisper 的 setup.py 仍依赖它。
# 由于 pip install -r 是整体事务，一个包失败导致 numpy 等基础包也没装上，
# 进而步骤 7 的 modelscope 无法导入。
#
# 本脚本：
#   1. 装 setuptools<80 恢复 pkg_resources
#   2. 先装基础包（numpy 等），保证后续可诊断
#   3. 过滤掉推理不需要的重包（openai-whisper / deepspeed / tensorrt）
#   4. 装剩余依赖
#   5. 验证 import 并下载模型
set -uo pipefail   # 注意：不用 -e，允许单步失败后继续诊断

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; }

COSYVOICE_DIR="${HOME}/CosyVoice"
VENV_DIR="${HOME}/cosyvoice-venv"
MODEL_DIR="${HOME}/models/Fun-CosyVoice3-0.5B-2512"
MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"

[ -d "${COSYVOICE_DIR}" ] || { fail "找不到 ${COSYVOICE_DIR}"; exit 1; }
[ -d "${VENV_DIR}" ] || { fail "找不到 ${VENV_DIR}"; exit 1; }

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
info "Python: $(python --version 2>&1) | $(which python)"

# ---------------------------------------------------------------- 1. 修 pkg_resources
info "步骤 1/5：修复 setuptools（恢复 pkg_resources）"
pip install -q "setuptools<80" wheel -i "${MIRROR}" || fail "setuptools 安装失败"
python -c "import pkg_resources; print('  pkg_resources OK')" \
    || fail "pkg_resources 仍不可用"

# ---------------------------------------------------------------- 2. 基础包
info "步骤 2/5：安装基础包"
# 这些是后续一切的前提，单独装，避免被其他包拖累
pip install -q \
    "numpy==1.26.4" \
    "PyYAML" "requests" "tqdm" "regex" "six" \
    -i "${MIRROR}" || fail "基础包安装失败"
python -c "import numpy; print('  numpy', numpy.__version__)"

# ---------------------------------------------------------------- 3. 过滤 requirements
info "步骤 3/5：生成过滤后的依赖清单"
cd "${COSYVOICE_DIR}"

# .bak 是首次运行时保存的原始文件（含 torch==2.3.1）
if [ -f requirements.txt.bak ]; then
    SRC="requirements.txt.bak"
elif [ -f requirements.txt ]; then
    SRC="requirements.txt"
else
    fail "找不到 requirements.txt"
    exit 1
fi
info "源清单: ${SRC}"

# 排除项说明：
#   torch/torchaudio   已单独装 2.8.0+cu128，不能让它降级
#   openai-whisper     构建失败且 CosyVoice3 推理不需要
#   deepspeed          训练用，Linux 上会装但推理不需要
#   tensorrt-*         Linux 专属，体积大，推理不强制
#   onnxruntime-gpu    与 torch 的 CUDA 运行库可能冲突，CosyVoice3 走 torch 路径
grep -v -E '^(torch==|torchaudio==|openai-whisper==|deepspeed==|tensorrt-|onnxruntime-gpu==)' \
    "${SRC}" > requirements_filtered.txt

info "过滤后清单（前 15 行）："
head -15 requirements_filtered.txt | sed 's/^/    /'

# ---------------------------------------------------------------- 4. 安装
info "步骤 4/5：安装依赖（耗时较长，请耐心等待）"
pip install -r requirements_filtered.txt -i "${MIRROR}"
PIP_RC=$?

if [ ${PIP_RC} -ne 0 ]; then
    warn "整体安装返回 ${PIP_RC}，可能有部分包失败"
    warn "尝试逐行安装，跳过失败的包"
    while IFS= read -r line; do
        [ -z "${line}" ] && continue
        case "${line}" in \#*) continue ;; esac
        pkg="${line%%#*}"
        pkg="$(echo "${pkg}" | xargs)"
        [ -z "${pkg}" ] && continue
        pip install -q "${pkg}" -i "${MIRROR}" 2>/dev/null \
            && echo "  OK   ${pkg}" \
            || echo "  SKIP ${pkg}"
    done < requirements_filtered.txt
fi

# ---------------------------------------------------------------- 5. 验证
info "步骤 5/5：验证关键 import"
cd "${COSYVOICE_DIR}"
python - <<'PYEOF'
import sys
sys.path.append('third_party/Matcha-TTS')
ok = True
for mod in ["torch", "torchaudio", "numpy", "modelscope", "onnxruntime",
            "librosa", "soundfile", "transformers", "hydra", "omegaconf"]:
    try:
        __import__(mod)
        print(f"  OK   {mod}")
    except Exception as e:
        print(f"  FAIL {mod}: {type(e).__name__}: {e}")
        ok = False
try:
    from cosyvoice.cli.cosyvoice import CosyVoice3
    print("  OK   cosyvoice.cli.cosyvoice.CosyVoice3")
except Exception as e:
    print(f"  FAIL cosyvoice: {type(e).__name__}: {e}")
    ok = False
sys.exit(0 if ok else 1)
PYEOF
VERIFY_RC=$?

# ---------------------------------------------------------------- 模型下载
echo
info "下载 CosyVoice3 模型（约 1-2 GB）"
mkdir -p "$(dirname "${MODEL_DIR}")"
if [ -d "${MODEL_DIR}" ] && [ -n "$(ls -A "${MODEL_DIR}" 2>/dev/null)" ]; then
    warn "模型目录已存在，跳过: ${MODEL_DIR}"
else
    python - <<PYEOF
from modelscope import snapshot_download
p = snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512', local_dir='${MODEL_DIR}')
print('模型下载完成:', p)
PYEOF
fi

echo
if [ ${VERIFY_RC} -eq 0 ]; then
    info "================ 修复完成 ================"
else
    warn "================ 部分模块仍缺失，请看上面的 FAIL ================"
fi
echo "  虚拟环境: ${VENV_DIR}"
echo "  仓库    : ${COSYVOICE_DIR}"
echo "  模型    : ${MODEL_DIR}"
echo
echo "下一步：最小合成验证"
echo "  source ${VENV_DIR}/bin/activate"
echo "  cd ${COSYVOICE_DIR}"
echo "  bash /mnt/d/xiangmudaima/zjy13/scripts/test_cosyvoice_wsl.sh"
