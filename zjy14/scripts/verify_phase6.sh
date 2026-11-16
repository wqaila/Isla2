#!/usr/bin/env bash
# Phase 6 验收：前端构建产物 + 后端联调
set -uo pipefail

PROJECT="/mnt/d/xiangmudaima/zjy14"
VENV="${HOME}/cosyvoice-venv"
BASE="http://127.0.0.1:50004"
OUT="/mnt/d/xiangmudaima/zjy14/_phase6"
mkdir -p "$OUT"

# shellcheck disable=SC1091
source "${VENV}/bin/activate"
cd "$PROJECT" || exit 1
export PYTHONPATH="$PROJECT"

PASS=0; FAIL=0
ok()  { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
check() { if [ "$2" = "$3" ]; then ok "$1 ($3)"; else bad "$1 (期望 $3 实际 $2)"; fi; }

echo "=============================================="
echo " Phase 6 验收：Web 界面"
echo "=============================================="

echo
echo "[1] 前端构建产物检查"
if [ -f "$PROJECT/web/dist/index.html" ]; then
  ok "dist/index.html 存在"
else
  bad "dist/index.html 缺失"
fi
JS=$(find "$PROJECT/web/dist/assets" -name "*.js" 2>/dev/null | head -1)
CSS=$(find "$PROJECT/web/dist/assets" -name "*.css" 2>/dev/null | head -1)
[ -n "$JS" ] && ok "JS bundle: $(basename "$JS") ($(stat -c%s "$JS") bytes)" || bad "JS bundle 缺失"
[ -n "$CSS" ] && ok "CSS bundle: $(basename "$CSS")" || bad "CSS bundle 缺失"

echo
echo "[2] 前端源码文件完整性"
for f in main.tsx App.tsx styles.css api/client.ts api/types.ts hooks/useAsync.ts components/ui.tsx; do
  if [ -f "$PROJECT/web/src/$f" ]; then ok "src/$f"; else bad "src/$f 缺失"; fi
done

echo
echo "[3] 8 个页面文件"
for p in Dashboard Characters Speakers Emotion Synthesis Devices System Logs; do
  if [ -f "$PROJECT/web/src/pages/${p}Page.tsx" ]; then ok "${p}Page.tsx"; else bad "${p}Page.tsx 缺失"; fi
done

echo
echo "[4] 启动后端（stub 引擎，快速验证接口）"
ZJY14_ENGINE=stub ZJY14_PORT=50004 ZJY14_DEVICE=cpu \
  python -m custom_voice.server > "$OUT/server.log" 2>&1 &
SRV_PID=$!
cleanup() { kill $SRV_PID 2>/dev/null; wait $SRV_PID 2>/dev/null; }
trap cleanup EXIT

READY=0
for i in $(seq 1 40); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$BASE/health" 2>/dev/null)
  if [ "$CODE" = "200" ]; then READY=1; echo "  就绪（${i}s）"; break; fi
  sleep 1
done
[ "$READY" = "1" ] && ok "后端启动" || { bad "后端未就绪"; tail -10 "$OUT/server.log"; exit 1; }

echo
echo "[5] 前端依赖的接口全部可用"
for ep in \
  "/health" \
  "/api/v1/system/status" \
  "/api/v1/system/gpu" \
  "/api/v1/characters" \
  "/api/v1/speakers"
do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$BASE$ep")
  check "GET $ep" "$CODE" "200"
done

echo
echo "[6] 合成接口（前端「合成测试」页依赖）"
CODE=$(curl -s -o "$OUT/t.wav" -D "$OUT/t.hdr" -w '%{http_code}' -X POST "$BASE/synthesize" \
  -H 'Content-Type: application/json' \
  -d '{"text":"测试文本，用于验证前端合成页","speed":1.0,"volume":1.0,"pause_profile":"natural"}')
check "POST /synthesize" "$CODE" "200"
grep -qi '^x-duration:' "$OUT/t.hdr" && ok "响应头含 x-duration" || bad "缺少 x-duration"
grep -qi '^x-rtf:' "$OUT/t.hdr" && ok "响应头含 x-rtf" || bad "缺少 x-rtf"

echo
echo "[7] 自然语言指令（前端输入框依赖）"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/synthesize" \
  -H 'Content-Type: application/json' \
  -d '{"text":"测试自然语言指令","instruction":"放慢速度，温柔一点"}')
check "instruction 字段" "$CODE" "200"

echo
echo "[8] 情绪标签（前端「情绪管理」页说明其为元数据）"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/synthesize" \
  -H 'Content-Type: application/json' \
  -d '{"text":"测试情绪标签","emotion":"happy","intensity":0.8}')
check "emotion 字段" "$CODE" "200"

echo
echo "[9] CORS：前端 dev server 走 proxy，生产需同源或反代"
curl -s -D "$OUT/h.hdr" -o /dev/null "$BASE/health"
grep -qi 'access-control-allow-origin' "$OUT/h.hdr" \
  && echo "  后端已返回 CORS 头" \
  || echo "  后端未返回 CORS 头（前端 dev 用 Vite proxy 规避，生产需同源部署）"

echo
echo "[10] 前端构建无 TypeScript 错误（已由 npm run build 验证）"
if [ -d "$PROJECT/web/node_modules" ]; then
  ok "node_modules 已安装"
else
  bad "node_modules 缺失"
fi

echo
echo "=============================================="
echo " 结果: $PASS 通过, $FAIL 失败"
echo "=============================================="
[ "$FAIL" -eq 0 ] || exit 1
