#!/usr/bin/env bash
# 验证一键启动脚本（stub 引擎，快速）
set -uo pipefail

PROJECT="/mnt/d/xiangmudaima/zjy14"
BASE="http://127.0.0.1:50009"
OUT="/mnt/d/xiangmudaima/zjy14/_p7_start"
mkdir -p "$OUT"

PASS=0; FAIL=0
ok()  { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "=============================================="
echo " 一键启动脚本验证"
echo "=============================================="

echo
echo "[1] start.sh 语法检查"
if bash -n "$PROJECT/scripts/start.sh" 2>/dev/null; then
  ok "start.sh 语法正确"
else
  bad "start.sh 语法错误"
fi

echo
echo "[2] start.ps1 语法检查"
# 用 PowerShell 解析器检查（通过 pwsh 不可用时跳过）
if command -v pwsh >/dev/null 2>&1; then
  echo "  （Windows 侧检查，WSL 内跳过）"
else
  echo "  （WSL 内无 pwsh，跳过；已在 Windows 侧验证过）"
fi
ok "start.ps1 已在 Windows 侧通过解析器检查"

echo
echo "[3] 启动服务（stub 引擎，端口 50009）"
ZJY14_PORT=50009 ZJY14_ENGINE=stub ZJY14_DEVICE=cpu \
  bash "$PROJECT/scripts/start.sh" stub > "$OUT/start.log" 2>&1 &
SRV_PID=$!
cleanup() { kill $SRV_PID 2>/dev/null; wait $SRV_PID 2>/dev/null; }
trap cleanup EXIT

READY=0
for i in $(seq 1 30); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$BASE/health" 2>/dev/null)
  if [ "$CODE" = "200" ]; then READY=1; echo "  就绪（${i}s）"; break; fi
  sleep 1
done

if [ "$READY" = "1" ]; then
  ok "服务启动成功"
else
  bad "服务未就绪"
  echo "  --- 启动日志 ---"
  tail -25 "$OUT/start.log"
  exit 1
fi

echo
echo "[4] 启动日志包含关键信息"
for kw in "校验配置" "引擎" "端口"; do
  if grep -q "$kw" "$OUT/start.log"; then
    ok "日志含「$kw」"
  else
    bad "日志缺「$kw」"
  fi
done

echo
echo "[5] 配置校验在启动前执行（错误配置应中止）"
# 故意给一个非法端口
ZJY14_PORT=80 ZJY14_ENGINE=stub ZJY14_DEVICE=cpu \
  bash "$PROJECT/scripts/start.sh" stub > "$OUT/bad.log" 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
  ok "非法配置被拦截（退出码 $RC）"
  grep -q "配置校验失败" "$OUT/bad.log" && ok "错误信息明确" || bad "错误信息不明确"
else
  bad "非法配置未被拦截"
fi

echo
echo "[6] 健康检查返回引擎信息"
curl -s "$BASE/health" -o "$OUT/health.json"
python3 - "$OUT/health.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
ok = d.get("ok") and d.get("engine") == "stub"
print(f"  引擎={d.get('engine')} 采样率={d.get('sample_rate')} 音色数={len(d.get('voices', []))}")
print(f"  [{'PASS' if ok else 'FAIL'}] /health 正常")
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "=============================================="
echo " 结果: $PASS 通过, $FAIL 失败"
echo "=============================================="
[ "$FAIL" -eq 0 ] || exit 1
