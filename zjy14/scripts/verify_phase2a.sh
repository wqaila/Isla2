#!/usr/bin/env bash
# Phase 2a 端到端验收：真模型启动 + 合成 + 稳定性
#
# 用法：wsl -e bash /mnt/d/xiangmudaima/zjy14/scripts/verify_phase2a.sh
set -uo pipefail

PROJECT="/mnt/d/xiangmudaima/zjy14"
VENV="${HOME}/cosyvoice-venv"
BASE="http://127.0.0.1:50002"
OUT="/mnt/d/xiangmudaima/zjy14/_phase2a"
mkdir -p "$OUT"

# shellcheck disable=SC1091
source "${VENV}/bin/activate"
cd "$PROJECT" || exit 1
export PYTHONPATH="$PROJECT"

PASS=0; FAIL=0
ok()  { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "=============================================="
echo " Phase 2a 验收：真模型端到端"
echo "=============================================="

# ---- 启动服务（cosyvoice3 引擎） ----
echo
echo "[0] 启动服务（engine=cosyvoice3, port=50002）"
ZJY14_ENGINE=cosyvoice3 ZJY14_PORT=50002 ZJY14_TEXT_FRONTEND=wetext \
  python -m custom_voice.server > "$OUT/server.log" 2>&1 &
SRV_PID=$!
echo "  PID=$SRV_PID，日志 $OUT/server.log"

cleanup() {
  kill $SRV_PID 2>/dev/null
  wait $SRV_PID 2>/dev/null
}
trap cleanup EXIT

# ---- 等待就绪（模型加载约 10-20s） ----
echo "  等待模型加载..."
READY=0
for i in $(seq 1 90); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$BASE/health" 2>/dev/null)
  if [ "$CODE" = "200" ]; then READY=1; echo "  就绪（等待 ${i}s）"; break; fi
  sleep 1
done

if [ "$READY" != "1" ]; then
  bad "服务未在 90s 内就绪"
  echo "  --- 日志尾部 ---"
  tail -30 "$OUT/server.log"
  exit 1
fi
ok "服务启动成功"

# ---- [1] /health ----
echo
echo "[1] /health 应暴露 text_frontend 与引擎信息"
curl -s "$BASE/health" -o "$OUT/health.json"
python - <<'PY'
import json
d = json.load(open("/mnt/d/xiangmudaima/zjy14/_phase2a/health.json"))
print(f"  引擎: {d.get('engine')}  采样率: {d.get('sample_rate')}")
print(f"  GPU: {d.get('gpu')}  torch: {d.get('torch')}")
print(f"  加载 {d.get('load_s')}s  预热 {d.get('warmup_s')}s")
print(f"  音色: {[v['voice_id'] for v in d.get('voices', [])]}")
diag = d.get("diagnostics", {})
print(f"  诊断: text_frontend={diag.get('text_frontend')!r} "
      f"voice_count={diag.get('voice_count')} precision={diag.get('precision')}")
assert d.get("engine") == "cosyvoice3", "引擎类型错误"
assert diag.get("text_frontend") == "wetext", "文本前端未启用"
assert diag.get("voice_count", 0) >= 1, "没有可用音色"
print("  [PASS] health 字段完整且 text_frontend=wetext")
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

# ---- [2] 合成 ----
echo
echo "[2] 合成真实语音"
curl -s -D "$OUT/hdr.txt" -o "$OUT/a.wav" -X POST "$BASE/synthesize" \
  -H 'Content-Type: application/json' \
  -d '{"text":"今天天气真不错，我们一起出去走走吧。","voice_id":"elysia","speed":1.0}' \
  -w '  HTTP %{http_code}  %{size_download} bytes\n'
for H in x-sample-rate x-duration x-total-ms x-rtf x-engine x-voice-id; do
  grep -qi "^$H:" "$OUT/hdr.txt" && ok "响应头 $H" || bad "缺少 $H"
done
python - <<'PY'
import wave
p = "/mnt/d/xiangmudaima/zjy14/_phase2a/a.wav"
with wave.open(p) as w:
    sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
print(f"  WAV: {sr}Hz {ch}ch {sw*8}bit {n/sr:.2f}s")
assert sr == 24000 and ch == 1 and sw == 2, "格式不符"
PY
[ $? -eq 0 ] && ok "WAV 格式正确（24kHz/单声道/16bit）" || bad "WAV 格式错误"

# ---- [3] 数字文本（验证 TN 生效） ----
echo
echo "[3] 含数字文本（TN 应转成中文读法）"
curl -s -o "$OUT/digits.wav" -X POST "$BASE/synthesize" \
  -H 'Content-Type: application/json' \
  -d '{"text":"现在是2026年11月9日，温度25度。","voice_id":"elysia"}' \
  -w '  HTTP %{http_code}  %{size_download} bytes\n'
[ -s "$OUT/digits.wav" ] && ok "数字文本合成成功" || bad "数字文本合成失败"

# ---- [4] 多次采样稳定性 ----
echo
echo "[4] 多次采样稳定性（5 次同一文本）"
python - <<'PY'
import json, subprocess, wave, statistics, urllib.request

url = "http://127.0.0.1:50002/synthesize"
durs, rtfs = [], []
for i in range(5):
    body = json.dumps({
        "text": "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。",
        "voice_id": "elysia",
    }).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
        durs.append(float(r.headers["x-duration"]))
        rtfs.append(float(r.headers["x-rtf"]))
    with open(f"/mnt/d/xiangmudaima/zjy14/_phase2a/stab_{i+1}.wav", "wb") as f:
        f.write(data)

print(f"  时长: {[round(d,2) for d in durs]}")
print(f"       均值 {statistics.mean(durs):.2f}s  标准差 {statistics.stdev(durs):.2f}s  "
      f"极差 {max(durs)-min(durs):.2f}s")
print(f"  RTF : {[round(r,3) for r in rtfs]}")
print(f"       均值 {statistics.mean(rtfs):.3f}")
cv = statistics.stdev(durs) / statistics.mean(durs)
print(f"  时长变异系数 {cv:.3f}")
if cv < 0.35:
    print("  [PASS] 采样稳定性在可接受范围（CV < 0.35）")
else:
    print(f"  [WARN] 采样波动较大（CV {cv:.3f}），评估时需多次采样")
PY

# ---- [5] 错误语义 ----
echo
echo "[5] 错误语义"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/synthesize" \
  -H 'Content-Type: application/json' -d '{"text":"   "}')
[ "$CODE" = "422" ] && ok "空文本 -> 422" || bad "空文本返回 $CODE"

CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/synthesize" \
  -H 'Content-Type: application/json' -d '{"text":"你好","bogus":1}')
[ "$CODE" = "422" ] && ok "多余字段 -> 422" || bad "多余字段返回 $CODE"

# ---- [6] /system/status ----
echo
echo "[6] /api/v1/system/status"
curl -s "$BASE/api/v1/system/status" -o "$OUT/status.json"
python - <<'PY'
import json
d = json.load(open("/mnt/d/xiangmudaima/zjy14/_phase2a/status.json"))
c = d["config"]
print(f"  engine={c['engine']} precision={c['precision']} "
      f"text_frontend={c.get('text_frontend', 'N/A')}")
print(f"  数据库表: {d['db']['tables']}")
assert "auth_token" not in c, "auth_token 泄露"
print("  [PASS] status 无敏感信息泄露")
PY

echo
echo "=============================================="
echo " 结果: $PASS 通过, $FAIL 失败"
echo "=============================================="
[ "$FAIL" -eq 0 ] || exit 1
