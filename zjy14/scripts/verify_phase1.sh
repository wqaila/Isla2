#!/usr/bin/env bash
# Phase 1 端到端验收脚本：逐项检查 docs/04_PHASE1_TASKS.md 第七节的完成标志
set -uo pipefail

BASE="${ZJY14_BASE:-http://127.0.0.1:50001}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
PASS=0
FAIL=0

ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

check() { if [ "$2" = "$3" ]; then ok "$1 ($3)"; else bad "$1 (期望 $3，实际 $2)"; fi; }

echo "=============================================="
echo " Phase 1 验收检查  $BASE"
echo "=============================================="

echo
echo "[1] /health 返回 200 且 ok=true"
CODE=$(curl -s -o "$TMP/h.json" -w '%{http_code}' "$BASE/health")
check "HTTP 状态" "$CODE" "200"
OKVAL=$(python3 -c "import json;print(json.load(open('$TMP/h.json'))['ok'])" 2>/dev/null)
check "ok 字段" "$OKVAL" "True"

echo
echo "[2] /api/v1/system/gpu 返回真实显存"
CODE=$(curl -s -o "$TMP/g.json" -w '%{http_code}' "$BASE/api/v1/system/gpu")
check "HTTP 状态" "$CODE" "200"
TOTAL=$(python3 -c "import json;d=json.load(open('$TMP/g.json'));print(d.get('total_mb',0))" 2>/dev/null)
if [ "${TOTAL:-0}" -gt 0 ]; then ok "total_mb=$TOTAL"; else bad "total_mb 无效: $TOTAL"; fi

echo
echo "[3] POST /synthesize 返回合法 WAV + 6 个响应头"
CODE=$(curl -s -D "$TMP/hdr.txt" -o "$TMP/a.wav" -w '%{http_code}' \
  -X POST "$BASE/synthesize" -H 'Content-Type: application/json' \
  -d '{"text":"你好呀今天天气真不错","voice_id":"elysia","speed":1.0}')
check "HTTP 状态" "$CODE" "200"
for H in x-sample-rate x-duration x-total-ms x-rtf x-engine x-voice-id; do
  if grep -qi "^$H:" "$TMP/hdr.txt"; then ok "响应头 $H"; else bad "缺少响应头 $H"; fi
done
# WAV 魔数
MAGIC=$(head -c 4 "$TMP/a.wav")
check "WAV 魔数" "$MAGIC" "RIFF"

echo
echo "[4] 错误语义：未知音色 -> 400"
CODE=$(curl -s -o "$TMP/e.json" -w '%{http_code}' \
  -X POST "$BASE/synthesize" -H 'Content-Type: application/json' \
  -d '{"text":"你好","voice_id":"nobody"}')
check "HTTP 状态" "$CODE" "400"
ECODE=$(python3 -c "import json;print(json.load(open('$TMP/e.json'))['code'])" 2>/dev/null)
check "错误码" "$ECODE" "invalid_request"

echo
echo "[5] 错误语义：空文本 -> 422"
CODE=$(curl -s -o /dev/null -w '%{http_code}' \
  -X POST "$BASE/synthesize" -H 'Content-Type: application/json' -d '{"text":"   "}')
check "HTTP 状态" "$CODE" "422"

echo
echo "[6] 错误语义：多余字段 -> 422"
CODE=$(curl -s -o /dev/null -w '%{http_code}' \
  -X POST "$BASE/synthesize" -H 'Content-Type: application/json' \
  -d '{"text":"你好","bogus_knob":1}')
check "HTTP 状态" "$CODE" "422"

echo
echo "[7] request_id 透传"
RID=$(curl -s -D - -o /dev/null -X POST "$BASE/synthesize" \
  -H 'Content-Type: application/json' -H 'x-request-id: trace-abc-001' \
  -d '{"text":"你好"}' | grep -i '^x-request-id:' | tr -d '\r' | awk '{print $2}')
check "x-request-id" "$RID" "trace-abc-001"

echo
echo "[8] Phase 2 扩展字段可传（Phase 1 不生效但不报错）"
CODE=$(curl -s -o /dev/null -w '%{http_code}' \
  -X POST "$BASE/synthesize" -H 'Content-Type: application/json' \
  -d '{"text":"你好","emotion":"happy","intensity":0.8,"style":"gentle","instruction":"温柔一点"}')
check "HTTP 状态" "$CODE" "200"

echo
echo "[9] /api/v1/system/status 不泄露 auth_token 明文"
curl -s "$BASE/api/v1/system/status" -o "$TMP/s.json"
if grep -q '"auth_token"' "$TMP/s.json"; then bad "status 中出现 auth_token 明文"; else ok "auth_token 已脱敏"; fi
HASFLAG=$(python3 -c "import json;d=json.load(open('$TMP/s.json'));print('auth_token_set' in d['config'])" 2>/dev/null)
check "auth_token_set 字段" "$HASFLAG" "True"

echo
echo "=============================================="
echo " 结果: $PASS 通过, $FAIL 失败"
echo "=============================================="
[ "$FAIL" -eq 0 ] || exit 1
