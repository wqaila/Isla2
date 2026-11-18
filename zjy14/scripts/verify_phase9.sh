#!/usr/bin/env bash
# Phase 9 验收：合成记录与日志查询 API
set -uo pipefail

PROJECT="/mnt/d/xiangmudaima/zjy14"
VENV="${HOME}/cosyvoice-venv"
BASE="http://127.0.0.1:50012"
OUT="$PROJECT/_phase9"
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
echo " Phase 9 验收：合成记录与日志查询 API"
echo "=============================================="

echo
echo "[1] 源码与路由注册"
for f in "custom_voice/history/repository.py" "custom_voice/history/logs.py" \
         "custom_voice/schemas/history.py" "custom_voice/server/routes/history.py"; do
  if [ -f "$PROJECT/$f" ]; then ok "$f"; else bad "$f 缺失"; fi
done

echo
echo "[2] 路由已注册到 app"
if grep -q "app.include_router(history.router)" "$PROJECT/custom_voice/server/app.py"; then
  ok "history.router 已注册"
else
  bad "history.router 未注册"
fi

echo
echo "[3] 启动服务（真模型）"
ZJY14_ENGINE=cosyvoice3 ZJY14_PORT=50012 ZJY14_TEXT_FRONTEND=wetext \
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
echo "[4] 接口存在性"
for ep in "/api/v1/history" "/api/v1/history/stats" "/api/v1/logs" "/api/v1/logs/files"; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE$ep")
  check "GET $ep" "$CODE" "200"
done

echo
echo "[5] 路由顺序：/history/stats 不被 /history/{id} 吃掉"
RESP=$(curl -s "$BASE/api/v1/history/stats")
if echo "$RESP" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if 'total' in d else 1)" 2>/dev/null; then
  ok "stats 返回统计字段（未被路径参数吞掉）"
else
  bad "stats 被路径参数吞掉"
fi

echo
echo "[6] 端到端联调（真模型合成 → 查询 → 统计 → 清理）"
if python3 "$PROJECT/probe/_p9_history_e2e.py" "$BASE" > "$OUT/e2e.log" 2>&1; then
  ok "端到端全部通过"
  grep -E "^\[[0-9]+\]" "$OUT/e2e.log" | sed 's/^/    /'
else
  bad "端到端有失败项"
  tail -25 "$OUT/e2e.log"
fi

echo
echo "[7] 单元测试"
if python3 -m pytest tests/test_history_api.py -o addopts='' -q > "$OUT/unit.log" 2>&1; then
  N=$(grep -oE '[0-9]+ passed' "$OUT/unit.log" | head -1)
  ok "历史 API 单元测试 $N"
else
  bad "单元测试失败"
  tail -15 "$OUT/unit.log"
fi

echo
echo "[8] 前端源码"
for f in "web/src/pages/HistoryPage.tsx" "web/src/pages/LogsPage.tsx"; do
  if [ -f "$PROJECT/$f" ]; then ok "$f"; else bad "$f 缺失"; fi
done
if grep -q "historyApi" "$PROJECT/web/src/api/client.ts"; then
  ok "前端含 historyApi"
else
  bad "前端缺 historyApi"
fi
if grep -q "logsApi" "$PROJECT/web/src/api/client.ts"; then
  ok "前端含 logsApi"
else
  bad "前端缺 logsApi"
fi

echo
echo "[9] 构建产物"
if [ -f "$PROJECT/web/dist/index.html" ]; then
  JS=$(find "$PROJECT/web/dist/assets" -name "*.js" 2>/dev/null | head -1)
  ok "dist 存在（JS $(basename "${JS:-none}")）"
else
  bad "dist 缺失（需先 npm run build）"
fi

echo
echo "=============================================="
echo " 结果: $PASS 通过, $FAIL 失败"
echo "=============================================="
[ "$FAIL" -eq 0 ] || exit 1
