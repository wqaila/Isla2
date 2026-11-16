#!/usr/bin/env bash
# zjy14 一键启动（在 WSL 内执行）
#
# 用法：
#   wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/start.sh              # 默认 stub 引擎
#   wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/start.sh cosyvoice3   # 真模型
#
# 特性：
#   - 自动检查环境（venv / 模型 / 显存）
#   - 启动前做配置校验，配置错直接退出并说明原因
#   - 只监听 127.0.0.1（除非显式配置）
#   - 优雅退出（Ctrl+C）
set -uo pipefail

ENGINE="${1:-stub}"
PROJECT_DIR="/mnt/d/xiangmudaima/zjy14"
VENV="${HOME}/cosyvoice-venv"
MODEL_DIR="${HOME}/models/Fun-CosyVoice3-0.5B-2512"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

echo "=============================================="
echo "  zjy14 Custom Voice Engine"
echo "=============================================="

# ---- 环境检查 ----
[ -d "$VENV" ] || { error "虚拟环境不存在: $VENV"; exit 1; }
[ -d "$PROJECT_DIR" ] || { error "项目目录不存在: $PROJECT_DIR"; exit 1; }
# shellcheck disable=SC1091
source "${VENV}/bin/activate" || { error "无法激活虚拟环境"; exit 1; }
info "Python $(python -V 2>&1 | awk '{print $2}')"

# ---- 模型检查（仅真引擎需要）----
if [ "$ENGINE" = "cosyvoice3" ]; then
  if [ ! -d "$MODEL_DIR" ]; then
    error "模型目录不存在: $MODEL_DIR"
    echo "  请先下载模型，或检查 configs/gpu.yaml 的 model_dir"
    exit 1
  fi
  info "模型目录 $(du -sh "$MODEL_DIR" 2>/dev/null | cut -f1)"

  if command -v nvidia-smi >/dev/null 2>&1; then
    FREE=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1 | tr -d ' ')
    TOTAL=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1 | tr -d ' ')
    info "GPU 显存 ${FREE}MB 空闲 / ${TOTAL}MB 总量"
    if [ "${FREE:-0}" -lt 4096 ]; then
      warn "空闲显存不足 4GB，模型加载可能失败"
    fi
  else
    warn "nvidia-smi 不可用，跳过显存检查"
  fi
fi

# ---- 配置校验（启动前拦截错误配置）----
cd "$PROJECT_DIR" || exit 1
export PYTHONPATH="$PROJECT_DIR"

info "校验配置..."
if ! python -c "
import sys
sys.path.insert(0, '$PROJECT_DIR')
from custom_voice.config.loader import load_config
try:
    c = load_config(overrides={'engine': '$ENGINE'})
    print(f'  引擎={c.engine.value} 端口={c.port} 监听={c.host}')
    print(f'  并发={c.max_concurrent_requests} 队列={c.max_queue_size}')
    print(f'  流式={c.stream_enabled} 阈值={c.stream_char_threshold}')
    print(f'  文本前端={c.text_frontend.value} compile={c.enable_compile}')
except Exception as e:
    print(f'配置错误: {e}', file=sys.stderr)
    sys.exit(1)
"; then
  error "配置校验失败，已中止启动"
  exit 1
fi

# ---- 启动 ----
echo
info "启动服务（引擎=$ENGINE）"
echo "  API 文档: http://127.0.0.1:\${ZJY14_PORT:-50001}/docs"
echo "  健康检查: http://127.0.0.1:\${ZJY14_PORT:-50001}/health"
echo "  Ctrl+C 停止"
echo

export ZJY14_ENGINE="$ENGINE"
exec python -m custom_voice.server
