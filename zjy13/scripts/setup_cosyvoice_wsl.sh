#!/usr/bin/env bash
# CosyVoice 3.0 在 WSL2/Ubuntu 24.04 的安装脚本
#
# 用法：在 WSL 里执行
#   bash /mnt/d/xiangmudaima/zjy13/scripts/setup_cosyvoice_wsl.sh
#
# 设计说明：
# 1. Ubuntu 24.04 默认 Python 3.12，CosyVoice 官方要求 3.10，用 deadsnakes PPA 装
# 2. 官方 requirements.txt 锁 torch==2.3.1，不支持 RTX 5070 的 sm_120，
#    必须先装 torch 2.8.0+cu128，再删掉 requirements 里的 torch 行
# 3. 全程用清华源加速
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

COSYVOICE_DIR="${HOME}/CosyVoice"
VENV_DIR="${HOME}/cosyvoice-venv"
MODEL_DIR="${HOME}/models/Fun-CosyVoice3-0.5B-2512"
PIP_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"

# ---------------------------------------------------------------- 1. 系统依赖
info "步骤 1/7：安装系统依赖"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    software-properties-common curl wget git git-lfs \
    sox libsox-dev build-essential cmake \
    libsndfile1 ffmpeg unzip || fail "系统依赖安装失败"

git lfs install --skip-repo >/dev/null 2>&1 || true

# ---------------------------------------------------------------- 2. Python 3.10
info "步骤 2/7：检查 Python 3.10"
if command -v python3.10 >/dev/null 2>&1; then
    info "python3.10 已存在: $(python3.10 --version)"
else
    info "安装 python3.10（deadsnakes PPA）"
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3.10 python3.10-venv python3.10-dev
fi
python3.10 --version || fail "python3.10 不可用"

# ---------------------------------------------------------------- 3. 克隆仓库
info "步骤 3/7：克隆 CosyVoice"
if [ -d "${COSYVOICE_DIR}" ]; then
    warn "目录已存在，跳过克隆: ${COSYVOICE_DIR}"
else
    git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git "${COSYVOICE_DIR}"
fi
cd "${COSYVOICE_DIR}"
git submodule update --init --recursive || warn "子模块更新失败，稍后重试"

# ---------------------------------------------------------------- 4. 虚拟环境
info "步骤 4/7：创建虚拟环境"
if [ ! -d "${VENV_DIR}" ]; then
    python3.10 -m venv "${VENV_DIR}"
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
python --version
pip install --upgrade pip -q -i "${PIP_MIRROR}"

# ---------------------------------------------------------------- 5. torch（关键）
info "步骤 5/7：安装支持 sm_120 的 PyTorch"
# 注意：不能用官方 requirements 里的 torch==2.3.1，它不支持 RTX 5070 (sm_120)
pip install -q \
    torch==2.8.0 torchaudio==2.8.0 \
    --index-url https://download.pytorch.org/whl/cu128

info "验证 PyTorch GPU 与架构"
python - <<'PYEOF'
import sys
import torch
print(f"  torch      : {torch.__version__}")
print(f"  CUDA 版本  : {torch.version.cuda}")
print(f"  CUDA 可用  : {torch.cuda.is_available()}")
if not torch.cuda.is_available():
    print("  [FAIL] CUDA 不可用，请检查 WSL GPU 透传")
    sys.exit(1)
cap = torch.cuda.get_device_capability(0)
archs = torch.cuda.get_arch_list()
target = f"sm_{cap[0]}{cap[1]}"
print(f"  GPU        : {torch.cuda.get_device_name(0)}")
print(f"  计算能力   : {target}")
print(f"  编译架构   : {', '.join(archs)}")
print(f"  架构匹配   : {target in archs}")
if target not in archs:
    print("  [FAIL] PyTorch 未编译该架构，会静默回退到 PTX JIT")
    sys.exit(1)
# 真实算子验证
a = torch.randn(256, 256, device="cuda")
b = torch.randn(256, 256, device="cuda")
(a @ b).sum().item()
torch.cuda.synchronize()
print("  CUDA 算子  : OK")
PYEOF

# ---------------------------------------------------------------- 6. 其余依赖
info "步骤 6/7：安装其余依赖"
cd "${COSYVOICE_DIR}"
# 删掉 requirements 里的 torch/torchaudio，防止被降级回 2.3.1
if [ -f requirements.txt ]; then
    cp requirements.txt requirements.txt.bak
    sed -i '/^torch==/d; /^torchaudio==/d' requirements.txt
    info "已从 requirements.txt 移除 torch/torchaudio 锁定（备份为 .bak）"
fi

# tensorrt 是 linux 专属且体积大，CosyVoice 推理不强制需要
pip install -q -r requirements.txt -i "${PIP_MIRROR}" \
    || warn "部分依赖安装失败，请查看上面的报错"

# pynini 在部分环境编译失败，WeTextProcessing 需要它
info "尝试安装 WeTextProcessing（用于文本正则化）"
pip install -q WeTextProcessing -i "${PIP_MIRROR}" 2>/dev/null \
    || warn "WeTextProcessing 安装失败，将回退到 ttsfrd 或其他方式"

# ---------------------------------------------------------------- 7. 模型下载
info "步骤 7/7：下载 CosyVoice3 模型"
mkdir -p "$(dirname "${MODEL_DIR}")"
if [ -d "${MODEL_DIR}" ] && [ -n "$(ls -A "${MODEL_DIR}" 2>/dev/null)" ]; then
    warn "模型目录已存在，跳过下载: ${MODEL_DIR}"
else
    pip install -q modelscope -i "${PIP_MIRROR}"
    python - <<PYEOF
from modelscope import snapshot_download
snapshot_download(
    'FunAudioLLM/Fun-CosyVoice3-0.5B-2512',
    local_dir='${MODEL_DIR}',
)
print('模型下载完成: ${MODEL_DIR}')
PYEOF
fi

echo
info "================ 安装完成 ================"
echo "  仓库    : ${COSYVOICE_DIR}"
echo "  虚拟环境: ${VENV_DIR}"
echo "  模型    : ${MODEL_DIR}"
echo
echo "下一步验证："
echo "  source ${VENV_DIR}/bin/activate"
echo "  cd ${COSYVOICE_DIR}"
echo "  python -c \"import sys; sys.path.append('third_party/Matcha-TTS'); from cosyvoice.cli.cosyvoice import CosyVoice3; print('import OK')\""
