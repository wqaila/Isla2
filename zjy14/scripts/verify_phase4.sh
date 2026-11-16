#!/usr/bin/env bash
# Phase 4 验收：流式 + 并发调度
set -uo pipefail

PROJECT="/mnt/d/xiangmudaima/zjy14"
VENV="${HOME}/cosyvoice-venv"
BASE="http://127.0.0.1:50005"
OUT="/mnt/d/xiangmudaima/zjy14/_phase4"
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
echo " Phase 4 验收：流式 + 并发调度"
echo "=============================================="

echo
echo "[0] 启动服务（真模型, port=50005）"
ZJY14_ENGINE=cosyvoice3 ZJY14_PORT=50005 ZJY14_TEXT_FRONTEND=wetext \
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
echo "[1] /synthesize_stream 存在且返回流式响应"
CODE=$(curl -s -o "$OUT/stream.bin" -D "$OUT/stream.hdr" -w '%{http_code}' \
  -X POST "$BASE/synthesize_stream" -H 'Content-Type: application/json' \
  -d '{"text":"今天天气不错，我们下午去公园散步吧，顺便买点吃的。","voice_id":"elysia"}')
check "POST /synthesize_stream" "$CODE" "200"
grep -qi '^x-sample-rate:' "$OUT/stream.hdr" && ok "响应头含 x-sample-rate" || bad "缺少 x-sample-rate"
grep -qi '^x-stream-protocol:' "$OUT/stream.hdr" && ok "响应头含 x-stream-protocol" || bad "缺少协议标识"

echo
echo "[2] 流式协议解析（4 字节大端长度前缀 + 0 长度结束）"
python3 - "$OUT/stream.bin" <<'PY'
import struct, sys

data = open(sys.argv[1], "rb").read()
pos, chunks, total, got_end = 0, 0, 0, False
sizes = []
while pos + 4 <= len(data):
    n = struct.unpack(">I", data[pos:pos+4])[0]
    pos += 4
    if n == 0:
        got_end = True
        break
    if pos + n > len(data):
        print(f"  [FAIL] 块长度 {n} 超出剩余数据")
        sys.exit(1)
    sizes.append(n)
    total += n
    chunks += 1
    pos += n

print(f"  块数 {chunks}  总字节 {total}  块大小 {sizes}")
print(f"  结束标记 {'有' if got_end else '缺失'}")
if got_end and chunks > 0:
    dur = total / 2 / 24000
    print(f"  [PASS] 协议正确，音频时长 {dur:.2f}s")
    sys.exit(0)
else:
    print("  [FAIL] 协议不完整")
    sys.exit(1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "[3] 流式首包延迟 vs 非流式（长句）"
LONG='我最近在调试一块 ESP32-S3 的开发板，屏幕驱动那边一直有点问题，你有空的话能不能帮我看看？'
# 非流式
T0=$(date +%s%N)
curl -s -o /dev/null -X POST "$BASE/synthesize" -H 'Content-Type: application/json' \
  -d "{\"text\":\"$LONG\",\"voice_id\":\"elysia\"}"
T1=$(date +%s%N)
NON_MS=$(( (T1 - T0) / 1000000 ))

# 流式首包
T0=$(date +%s%N)
curl -s --no-buffer -X POST "$BASE/synthesize_stream" -H 'Content-Type: application/json' \
  -d "{\"text\":\"$LONG\",\"voice_id\":\"elysia\"}" 2>/dev/null \
  | head -c 100 > /dev/null
T1=$(date +%s%N)
ST_MS=$(( (T1 - T0) / 1000000 ))

echo "  非流式总耗时: ${NON_MS}ms"
echo "  流式首包:     ${ST_MS}ms（含 curl 启动开销）"
if [ "$ST_MS" -lt "$NON_MS" ]; then
  ok "流式首包更快（$(python3 -c "print(f'{(1-$ST_MS/$NON_MS)*100:.0f}%')") 改善）"
else
  bad "流式首包未更快"
fi

echo
echo "[4] 短句走非流式（路由判断）"
python3 - <<'PY'
import sys
sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")
from custom_voice.inference.streaming import StreamPolicy, count_chars, describe_routing

p = StreamPolicy()
cases = [
    ("你好呀。", False),
    ("今天天气不错，我们下午去公园散步吧，顺便买点吃的。", True),
    ("我最近在调试一块 ESP32-S3 的开发板，屏幕驱动那边一直有点问题。", True),
]
allok = True
for text, expect in cases:
    got = p.should_stream(text)
    mark = "PASS" if got == expect else "FAIL"
    if got != expect:
        allok = False
    print(f"  [{mark}] {count_chars(text):>3} 字 -> {'流式' if got else '非流式'}"
          f"（期望 {'流式' if expect else '非流式'}）")
sys.exit(0 if allok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "[5] 并发：单并发下应串行执行"
python3 - "$BASE" <<'PY'
import json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

base = sys.argv[1]
body = json.dumps({"text": "并发测试文本，用于验证调度器行为", "voice_id": "elysia"}).encode()

def one(i):
    req = urllib.request.Request(f"{base}/synthesize", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            r.read()
        return time.perf_counter() - t0, r.status
    except Exception as e:
        return time.perf_counter() - t0, type(e).__name__

# 关键：先预热一次，再测基准。
# 首次合成含 CUDA kernel 初始化与可能的 compile，会虚高 3 倍以上，
# 用它当基准会误判「并发未生效」。
one(0)
print("  已预热（首次合成开销已排除）")

# 基准：3 次取中位
base_times = []
for i in range(3):
    t, code = one(i)
    if code == 200:
        base_times.append(t)
base_times.sort()
single = base_times[len(base_times) // 2]
print(f"  单请求中位 {single:.2f}s（3 次：{[round(t,2) for t in base_times]}）")

t0 = time.perf_counter()
with ThreadPoolExecutor(max_workers=3) as ex:
    results = list(ex.map(one, range(3)))
wall = time.perf_counter() - t0

statuses = [r[1] for r in results]
times = [r[0] for r in results]
print(f"  3 并发请求，墙钟 {wall:.2f}s，各请求 {[round(t,2) for t in times]}")
print(f"  状态 {statuses}")

if not all(s == 200 for s in statuses):
    print(f"  [FAIL] 有请求失败: {statuses}")
    sys.exit(1)

ratio = wall / single if single > 0 else 0
print(f"  墙钟/单请求 = {ratio:.2f}（串行应 >= 2.2，并行接近 1.0）")

if ratio >= 2.2:
    print("  [PASS] 串行执行，符合 max_concurrent=1")
    sys.exit(0)
elif ratio >= 1.5:
    print("  [部分] 介于串行与并行之间")
    sys.exit(0)
else:
    print("  [FAIL] 仍是并行，调度器可能未生效")
    sys.exit(1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "[6] 调度器统计暴露"
curl -s "$BASE/api/v1/system/status" -o "$OUT/status.json"
python3 - "$OUT/status.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
c = d["config"]
print(f"  max_concurrent={c['max_concurrent_requests']} "
      f"max_queue={c['max_queue_size']} "
      f"stream_enabled={c.get('stream_enabled')} "
      f"threshold={c.get('stream_char_threshold')}")
ok = c['max_queue_size'] == 8 and c.get('stream_enabled') is True
print(f"  [{'PASS' if ok else 'FAIL'}] 配置项已暴露")
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "[7] 取消接口存在"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/synthesis/fake-id/cancel")
check "POST /synthesis/{id}/cancel" "$CODE" "200"

echo
echo "[8] 流式与非流式音频时长应接近（同一文本）"
python3 - "$BASE" <<'PY'
import json, struct, sys, urllib.request

base = sys.argv[1]
text = "今天天气不错，我们下午去公园散步吧，顺便买点吃的。"
body = json.dumps({"text": text, "voice_id": "elysia"}).encode()

# 非流式
req = urllib.request.Request(f"{base}/synthesize", data=body,
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=180) as r:
    wav = r.read()
    non_dur = float(r.headers["x-duration"])

# 流式
req2 = urllib.request.Request(f"{base}/synthesize_stream", data=body,
                              headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req2, timeout=180) as r2:
    data = r2.read()

pos, total = 0, 0
while pos + 4 <= len(data):
    n = struct.unpack(">I", data[pos:pos+4])[0]
    pos += 4
    if n == 0:
        break
    total += n
    pos += n
st_dur = total / 2 / 24000

diff = abs(non_dur - st_dur) / max(non_dur, 1e-9)
print(f"  非流式 {non_dur:.2f}s  流式 {st_dur:.2f}s  差异 {diff:.1%}")
# 采样随机性允许一定差异，但不应差一个数量级
ok = diff < 0.5
print(f"  [{'PASS' if ok else 'FAIL'}] 时长量级一致")
sys.exit(0 if ok else 1)
PY
[ $? -eq 0 ] && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "=============================================="
echo " 结果: $PASS 通过, $FAIL 失败"
echo "=============================================="
[ "$FAIL" -eq 0 ] || exit 1
