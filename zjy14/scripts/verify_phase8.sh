#!/usr/bin/env bash
# Phase 8 验收：前端流式播放
set -uo pipefail

PROJECT="/mnt/d/xiangmudaima/zjy14"
WEB="$PROJECT/web"
VENV="${HOME}/cosyvoice-venv"
BASE="http://127.0.0.1:50010"
OUT="$PROJECT/_phase8"
mkdir -p "$OUT"

PASS=0; FAIL=0
ok()  { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "=============================================="
echo " Phase 8 验收：前端流式播放"
echo "=============================================="

echo
echo "[1] 前端流式源码完整性"
for f in "src/api/client.ts" "src/audio/StreamPlayer.ts" "src/pages/SynthesisPage.tsx" "scripts/verify-stream.mjs"; do
  if [ -f "$WEB/$f" ]; then ok "$f"; else bad "$f 缺失"; fi
done

echo
echo "[2] client.ts 含流式解析关键逻辑"
python3 - "$WEB/src/api/client.ts" <<'PY'
import sys
src = open(sys.argv[1], encoding="utf-8").read()
checks = [
    ("synthesizeStream 方法", "synthesizeStream" in src),
    ("大端长度前缀解析", "getUint32(0, false)" in src),
    ("结束标记判断", "len === 0" in src),
    ("截断检测", "stream_truncated" in src),
    ("采样率响应头", "x-sample-rate" in src),
]
allok = True
for name, got in checks:
    print(f"  [{'PASS' if got else 'FAIL'}] {name}")
    if not got:
        allok = False
sys.exit(0 if allok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "[3] StreamPlayer 含 Web Audio 调度逻辑"
python3 - "$WEB/src/audio/StreamPlayer.ts" <<'PY'
import sys
src = open(sys.argv[1], encoding="utf-8").read()
checks = [
    ("AudioContext", "AudioContext" in src),
    ("逐块排期", "nextStartTime" in src),
    ("PCM16 -> Float32", "32768" in src),
    ("停止清理", "stop()" in src),
    ("suspended 恢复", "resume()" in src),
]
allok = True
for name, got in checks:
    print(f"  [{'PASS' if got else 'FAIL'}] {name}")
    if not got:
        allok = False
sys.exit(0 if allok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "[4] TypeScript 类型检查"
cd "$WEB" || exit 1
if [ -d node_modules ]; then
  if npm run typecheck > "$OUT/typecheck.log" 2>&1; then
    ok "tsc --noEmit 通过"
  else
    bad "类型检查失败"
    tail -10 "$OUT/typecheck.log"
  fi
else
  bad "node_modules 缺失"
fi

echo
echo "[5] 生产构建"
if npm run build > "$OUT/build.log" 2>&1; then
  JS=$(find dist/assets -name "*.js" 2>/dev/null | head -1)
  SIZE=$(stat -c%s "$JS" 2>/dev/null || echo 0)
  ok "构建成功（JS ${SIZE} 字节）"
else
  bad "构建失败"
  tail -15 "$OUT/build.log"
fi

echo
echo "[6] 启动后端（真模型）"
cd "$PROJECT" || exit 1
# shellcheck disable=SC1091
source "${VENV}/bin/activate"
export PYTHONPATH="$PROJECT"
ZJY14_ENGINE=cosyvoice3 ZJY14_PORT=50010 ZJY14_TEXT_FRONTEND=wetext \
  python -m custom_voice.server > "$OUT/server.log" 2>&1 &
SRV_PID=$!
cleanup() { kill $SRV_PID 2>/dev/null; wait $SRV_PID 2>/dev/null; }
trap cleanup EXIT

echo "  等待模型加载..."
READY=0
for i in $(seq 1 90); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$BASE/health" 2>/dev/null)
  if [ "$CODE" = "200" ]; then READY=1; echo "  就绪（${i}s）"; break; fi
  sleep 1
done
[ "$READY" = "1" ] && ok "服务启动" || { bad "服务未就绪"; tail -20 "$OUT/server.log"; exit 1; }

echo
echo "[7] 流式协议端到端（与前端相同算法）"
# node 通常只在 Windows 侧，WSL 内不一定有。
# 若 WSL 无 node，提示从 Windows 侧单独运行。
if command -v node >/dev/null 2>&1; then
  cd "$WEB" || exit 1
  if ZJY14_BASE="$BASE" node scripts/verify-stream.mjs > "$OUT/stream.log" 2>&1; then
    ok "流式解析验证通过"
    grep -E "^  (短句|中句|长句)" "$OUT/stream.log" | while read -r line; do
      echo "    $line"
    done
  else
    bad "流式解析验证失败"
    tail -20 "$OUT/stream.log"
  fi
else
  echo "  [SKIP] WSL 内无 node"
  echo "         请在 Windows 侧运行："
  echo "           cd D:\\xiangmudaima\\zjy14\\web"
  echo "           node scripts\\verify-stream.mjs"
  echo "         或先启动后端（端口 50001），再跑上述命令。"
fi

echo
echo "[8] 首块延迟必须显著小于总耗时"
if [ ! -f "$OUT/stream.log" ]; then
  echo "  [SKIP] 无流式日志（需在 Windows 侧运行 node 验证）"
else
python3 - "$OUT/stream.log" <<'PY'
import re, sys

text = open(sys.argv[1], encoding="utf-8").read()
rows = re.findall(
    r"(短句_\d+字|中句_\d+字|长句_\d+字)\s+(\d+)\s+([\d.]+)s\s+(\d+)\s+(\d+)\s+([\d.]+)%",
    text,
)
if not rows:
    print("  [FAIL] 未解析到汇总数据")
    sys.exit(1)

allok = True
for label, chunks, dur, first_ms, oneshot_ms, imp in rows:
    first_ms = int(first_ms)
    oneshot_ms = int(oneshot_ms)
    ok = first_ms < oneshot_ms * 0.85
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: 首块 {first_ms}ms "
          f"vs 非流式 {oneshot_ms}ms（快 {imp}%）")
    if not ok:
        allok = False
sys.exit(0 if allok else 1)
PY
  [ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))
fi

echo
echo "=============================================="
echo " 结果: $PASS 通过, $FAIL 失败"
echo "=============================================="
[ "$FAIL" -eq 0 ] || exit 1
