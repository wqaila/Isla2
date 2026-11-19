#!/usr/bin/env bash
# 真机联调一键验收（Phase 5 补充：从"协议桥接"升级到"真机在环"）
#
# 背景
# ====
# docs/18_PHASE5_REPORT.md 里 Phase 5 的定位是"桥接验证"——协议对齐、端到端
# 模拟通过，但**没有接真板子**。docs/20 与 26 的日报里，P0 一直是
# "真机联调：需要 ESP32 硬件"，因此长期未做。
#
# 现在板子在线，本脚本把整条链路一次性验完：
#
#   ESP32 固件 ──WS──> zjy13 ws/server.py ──HTTP──> zjy14 /synthesize_stream
#
# 三步：
#   1. probe/_p8_device_check.py   —— 六项链路诊断（含真机在线判定）
#   2. probe/_verify_zjy13_tts.py  —— 用 zjy13 生产代码的 TTSClient 实打一次
#   3. 汇总
#
# 前置条件
# ========
#   - zjy14 引擎在跑（.\scripts\start.ps1 cosyvoice3）
#   - zjy13 WS 服务在跑（Windows 侧，端口 8765）
#   - 板子已连 WiFi 并握手（zjy13 /api/devices 能看到 clients）
#
# 用法
# ====
#   wsl -e bash /mnt/d/xiangmudaima/zjy14/probe/run_device_e2e.sh
#
# 注意：第 2 步要用 **zjy13 的** python（它的依赖装在 zjy13/.venv-asr），
# 不是 zjy14 的 venv。脚本会自动挑选可用的那个。
set -uo pipefail

ZJY14_DIR="/mnt/d/xiangmudaima/zjy14"
ZJY13_DIR="/mnt/d/xiangmudaima/zjy13"
ZJY13_WIN_DIR="D:/xiangmudaima/zjy13"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

FAILED=0

echo "================================================================"
echo " 真机联调一键验收"
echo "================================================================"

# ---- Step 1: 链路诊断 ----
echo
info "Step 1/2 链路诊断（六项）"
echo "----------------------------------------------------------------"
cd "$ZJY14_DIR" || exit 1
if bash "$ZJY14_DIR/probe/run_d1.sh" "$ZJY14_DIR/probe/_p8_device_check.py"; then
  info "Step 1 通过"
else
  error "Step 1 未通过"
  FAILED=1
fi

# ---- Step 2: 用 zjy13 生产代码实打 ----
echo
info "Step 2/2 用 zjy13 的 TTSClient 实打一次"
echo "----------------------------------------------------------------"

# zjy13 的依赖在它自己的 venv 里。优先用 Windows 侧的 .venv-asr，
# 因为它装了 numpy/funasr 等 ASR 依赖；WSL 侧则看是否有可用 python。
PY13=""
for cand in \
  "$ZJY13_DIR/.venv-asr/bin/python" \
  "$HOME/cosyvoice-venv/bin/python"
do
  if [ -x "$cand" ]; then PY13="$cand"; break; fi
done

if [ -z "$PY13" ]; then
  warn "WSL 里找不到 zjy13 的 python 虚拟环境"
  echo "       在 Windows 侧跑这一步："
  echo "         cd D:\\xiangmudaima\\zjy13"
  echo "         .\\venv-asr\\Scripts\\python.exe ..\\zjy14\\probe\\_verify_zjy13_tts.py"
  warn "Step 2 跳过（不算失败，但真机声音未验证）"
else
  info "使用 python: $PY13"
  if ZJY13_ROOT="$ZJY13_DIR" "$PY13" "$ZJY14_DIR/probe/_verify_zjy13_tts.py"; then
    info "Step 2 通过"
  else
    error "Step 2 未通过"
    FAILED=1
  fi
fi

# ---- 汇总 ----
echo
echo "================================================================"
if [ "$FAILED" -eq 0 ]; then
  echo -e " ${GREEN}全部通过${NC}：板子听到的是 zjy14（CosyVoice3）的声音。"
  echo "================================================================"
  exit 0
fi
echo -e " ${RED}存在未通过项${NC}，按上面的提示逐条排查。"
echo "================================================================"
exit 1
