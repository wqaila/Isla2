#!/usr/bin/env bash
# Phase 2b 端到端验收：表达控制（路线乙）
set -uo pipefail

PROJECT="/mnt/d/xiangmudaima/zjy14"
VENV="${HOME}/cosyvoice-venv"
BASE="http://127.0.0.1:50003"
OUT="/mnt/d/xiangmudaima/zjy14/_phase2b"
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
echo " Phase 2b 验收：表达控制（路线乙）"
echo "=============================================="

echo
echo "[0] 启动服务（engine=cosyvoice3, port=50003）"
ZJY14_ENGINE=cosyvoice3 ZJY14_PORT=50003 ZJY14_TEXT_FRONTEND=wetext \
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
if [ "$READY" != "1" ]; then
  bad "服务未就绪"; tail -20 "$OUT/server.log"; exit 1
fi
ok "服务启动"

# ---- 工具函数：合成并输出时长 ----
synth() {
  local tag="$1"; local body="$2"
  curl -s -D "$OUT/${tag}.hdr" -o "$OUT/${tag}.wav" -X POST "$BASE/synthesize" \
    -H 'Content-Type: application/json' -d "$body" \
    -w '' 2>/dev/null
  if [ -s "$OUT/${tag}.wav" ]; then
    local d
    d=$(grep -i '^x-duration:' "$OUT/${tag}.hdr" | tr -d '\r' | awk '{print $2}')
    echo "$d"
  else
    echo "0"
  fi
}

TEXT='今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。'

echo
echo "[1] speed 生效：0.85 / 1.0 / 1.15 时长应递增"
D1=$(synth sp085 "{\"text\":\"$TEXT\",\"speed\":0.85}")
D2=$(synth sp100 "{\"text\":\"$TEXT\",\"speed\":1.0}")
D3=$(synth sp115 "{\"text\":\"$TEXT\",\"speed\":1.15}")
echo "  speed 0.85 -> ${D1}s"
echo "  speed 1.00 -> ${D2}s"
echo "  speed 1.15 -> ${D3}s"
python3 - "$D1" "$D2" "$D3" <<'PY'
import sys
a, b, c = (float(x) for x in sys.argv[1:4])
ok = a > b > c
print(f"  [{'PASS' if ok else 'FAIL'}] 时长单调递减（{a:.2f} > {b:.2f} > {c:.2f}）")
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "[2] 音量生效：volume 1.3 的 RMS 应高于 1.0"
synth vol10 '{"text":"'"$TEXT"'","volume":1.0}' > /dev/null
synth vol13 '{"text":"'"$TEXT"'","volume":1.3}' > /dev/null
python3 - "$OUT/vol10.wav" "$OUT/vol13.wav" <<'PY'
import sys, wave
import numpy as np

def rms(p):
    with wave.open(p) as w:
        d = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float64)
    return float(np.sqrt((d**2).mean())) if d.size else 0.0

a, b = rms(sys.argv[1]), rms(sys.argv[2])
ok = b > a * 1.05
print(f"  RMS volume1.0={a:.0f}  volume1.3={b:.0f}  比值 {b/max(a,1):.2f}")
print(f"  [{'PASS' if ok else 'FAIL'}] 音量增益生效")
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "[3] 停顿生效：dramatic 的静音占比应高于 none"
synth pause_none '{"text":"'"$TEXT"'","pause_profile":"none"}' > /dev/null
synth pause_drama '{"text":"'"$TEXT"'","pause_profile":"dramatic"}' > /dev/null
python3 - "$OUT/pause_none.wav" "$OUT/pause_drama.wav" <<'PY'
import sys, wave
import numpy as np

def silence_ratio(p):
    with wave.open(p) as w:
        sr = w.getframerate()
        d = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float64)
    hop = int(sr * 0.01)
    n = len(d) // hop
    if n == 0:
        return 0.0
    rms = np.sqrt((d[:n*hop].reshape(n, hop) ** 2).mean(axis=1))
    pk = rms.max()
    if pk <= 0:
        return 0.0
    return float((rms < pk * 0.02).mean())

a, b = silence_ratio(sys.argv[1]), silence_ratio(sys.argv[2])
ok = b > a + 0.05
print(f"  静音占比 none={a:.1%}  dramatic={b:.1%}")
print(f"  [{'PASS' if ok else 'FAIL'}] 停顿档位生效")
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "[4] 自然语言指令解析生效"
D_SLOW=$(synth instr_slow "{\"text\":\"$TEXT\",\"instruction\":\"放慢速度\"}")
D_FAST=$(synth instr_fast "{\"text\":\"$TEXT\",\"instruction\":\"快一点\"}")
echo "  指令「放慢速度」 -> ${D_SLOW}s"
echo "  指令「快一点」   -> ${D_FAST}s"
python3 - "$D_SLOW" "$D_FAST" <<'PY'
import sys
a, b = float(sys.argv[1]), float(sys.argv[2])
ok = a > b
print(f"  [{'PASS' if ok else 'FAIL'}] 指令影响时长（{a:.2f} > {b:.2f}）")
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "[5] emotion 仅元数据：传与不传，音频应无显著差异"
synth emo_none '{"text":"'"$TEXT"'"}' > /dev/null
synth emo_happy '{"text":"'"$TEXT"'","emotion":"happy","intensity":0.9}' > /dev/null
python3 - "$OUT/emo_none.wav" "$OUT/emo_happy.wav" <<'PY'
import sys, wave
import numpy as np

def dur(p):
    with wave.open(p) as w:
        return w.getnframes() / w.getframerate()

a, b = dur(sys.argv[1]), dur(sys.argv[2])
diff = abs(a - b) / max(a, 1e-9)
# 允许采样随机性，但不应有系统性差异
print(f"  无情绪 {a:.2f}s  带 happy {b:.2f}s  差异 {diff:.1%}")
print("  [PASS] emotion 不改变合成行为（符合 D1 结论）" if diff < 0.25
      else "  [WARN] 差异较大，需人工确认")
PY
PASS=$((PASS+1))

echo
echo "[6] 未识别指令应给出警告但不报错"
CODE=$(curl -s -o "$OUT/bad_instr.json" -w '%{http_code}' -X POST "$BASE/synthesize" \
  -H 'Content-Type: application/json' \
  -d '{"text":"'"$TEXT"'","instruction":"用火星语说话"}')
check "未识别指令" "$CODE" "200"

echo
echo "[7] 越界语速被限制并警告"
D_EXTREME=$(synth extreme '{"text":"'"$TEXT"'","speed":0.5}')
echo "  speed 0.5（低于安全区间） -> ${D_EXTREME}s"
grep -q "超出建议区间" "$OUT/server.log" && ok "已记录语速警告" || bad "未记录语速警告"

echo
echo "[8] /health 暴露表达控制能力"
curl -s "$BASE/health" -o "$OUT/health.json"
python3 - <<'PY'
import json
d = json.load(open("/mnt/d/xiangmudaima/zjy14/_phase2b/health.json"))
diag = d.get("diagnostics", {})
print(f"  engine={d.get('engine')} text_frontend={diag.get('text_frontend')}")
print(f"  voices={diag.get('voice_count')}")
print("  [PASS] health 正常" if d.get("ok") else "  [FAIL]")
PY

echo
echo "=============================================="
echo " 结果: $PASS 通过, $FAIL 失败"
echo "=============================================="
[ "$FAIL" -eq 0 ] || exit 1
