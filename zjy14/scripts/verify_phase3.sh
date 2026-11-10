#!/usr/bin/env bash
# Phase 3 端到端验收：角色与声音管理
set -uo pipefail

BASE="${ZJY14_BASE:-http://127.0.0.1:50001}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0

ok()  { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
check() { if [ "$2" = "$3" ]; then ok "$1 ($3)"; else bad "$1 (期望 $3 实际 $2)"; fi; }

echo "=============================================="
echo " Phase 3 验收  $BASE"
echo "=============================================="

echo
echo "[1] 已迁移的 elysia 角色与声音可读"
CODE=$(curl -s -o "$TMP/c.json" -w '%{http_code}' "$BASE/api/v1/characters/elysia")
check "GET /characters/elysia" "$CODE" "200"
python3 -c "
import json;d=json.load(open('$TMP/c.json'))
print('  角色:', d['display_name'], '| 声音:', d['speaker_id'], '| 情绪:', d['allowed_emotions'])
" 2>/dev/null

CODE=$(curl -s -o "$TMP/s.json" -w '%{http_code}' "$BASE/api/v1/speakers/elysia")
check "GET /speakers/elysia" "$CODE" "200"
python3 -c "
import json;d=json.load(open('$TMP/s.json'))
print('  时长:', d['duration_s'], 's | 采样率:', d['sample_rate'], '| 缓存key:', d['feature_cache_key'][:8])
print('  质量评分:', d['quality_report'].get('score'))
" 2>/dev/null

echo
echo "[2] 创建角色（不绑声音）"
CODE=$(curl -s -o "$TMP/n.json" -w '%{http_code}' -X POST "$BASE/api/v1/characters" \
  -H 'Content-Type: application/json' \
  -d '{"character_id":"test_char","display_name":"测试角色","default_emotion":"happy"}')
check "POST /characters" "$CODE" "201"

echo
echo "[3] 重复创建 -> 400"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/characters" \
  -H 'Content-Type: application/json' \
  -d '{"character_id":"test_char","display_name":"重复"}')
check "重复 ID" "$CODE" "400"

echo
echo "[4] 局部更新保留其他字段"
curl -s -X POST "$BASE/api/v1/characters" -H 'Content-Type: application/json' \
  -d '{"character_id":"patch_test","display_name":"原名","personality":"活泼","default_style":"gentle"}' > /dev/null
curl -s -X PATCH "$BASE/api/v1/characters/patch_test" -H 'Content-Type: application/json' \
  -d '{"display_name":"改名"}' -o "$TMP/p.json"
python3 -c "
import json;d=json.load(open('$TMP/p.json'))
ok = d['display_name']=='改名' and d['personality']=='活泼' and d['default_style']=='gentle'
print('  [PASS] 未传字段保留' if ok else '  [FAIL] 字段被清空')
print('  display_name=%s personality=%s style=%s' % (d['display_name'], d['personality'], d['default_style']))
" 2>/dev/null
python3 -c "
import json;d=json.load(open('$TMP/p.json'))
exit(0 if d['personality']=='活泼' else 1)
" 2>/dev/null && PASS=$((PASS+1)) || FAIL=$((FAIL+1))

echo
echo "[5] 非法情绪标签 -> 422"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/characters" \
  -H 'Content-Type: application/json' \
  -d '{"character_id":"bad","display_name":"x","allowed_emotions":["ecstatic"]}')
check "未知情绪" "$CODE" "422"

echo
echo "[6] 非法 character_id -> 422"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/characters" \
  -H 'Content-Type: application/json' \
  -d '{"character_id":"a/b","display_name":"x"}')
check "路径字符" "$CODE" "422"

echo
echo "[7] 绑定不存在的音色 -> 400"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/characters" \
  -H 'Content-Type: application/json' \
  -d '{"character_id":"bind_ghost","display_name":"x","speaker_id":"ghost"}')
check "幽灵音色" "$CODE" "400"

echo
echo "[8] 路径穿越拒绝"
CODE=$(curl -s -o "$TMP/e.json" -w '%{http_code}' -X POST "$BASE/api/v1/speakers" \
  -H 'Content-Type: application/json' \
  -d '{"speaker_id":"evil","audio_path":"/tmp/../etc/passwd.wav","transcript":"x"}')
check "路径穿越" "$CODE" "400"

echo
echo "[9] 不存在的音频文件 -> 400"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/speakers" \
  -H 'Content-Type: application/json' \
  -d '{"speaker_id":"nofile","audio_path":"/tmp/nonexistent_xyz.wav","transcript":"x"}')
check "文件不存在" "$CODE" "400"

echo
echo "[10] 空参考文本 -> 422（零样本克隆必需）"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/v1/speakers" \
  -H 'Content-Type: application/json' \
  -d '{"speaker_id":"empty","audio_path":"/tmp/a.wav","transcript":"   "}')
check "空文本" "$CODE" "422"

echo
echo "[11] 低质量音频被拒绝（1 秒素材）"
# 注意：必须用 venv 里的 python（系统 python 没有 numpy/soundfile）
PYBIN="${HOME}/cosyvoice-venv/bin/python"
[ -x "$PYBIN" ] || PYBIN=$(command -v python3)
"$PYBIN" - "$TMP/short.wav" <<'PY'
import sys, numpy as np, soundfile as sf
sr = 24000
t = np.arange(int(1.0*sr))/sr
sf.write(sys.argv[1], (np.sin(2*np.pi*220*t)*0.5).astype(np.float32), sr, subtype='PCM_16')
PY
CODE=$(curl -s -o "$TMP/q.json" -w '%{http_code}' -X POST "$BASE/api/v1/speakers" \
  -H 'Content-Type: application/json' \
  -d "{\"speaker_id\":\"short\",\"audio_path\":\"$TMP/short.wav\",\"transcript\":\"x\"}")
check "过短素材" "$CODE" "400"
python3 -c "
import json;d=json.load(open('$TMP/q.json'))
print('  拒绝原因:', d['detail']['blocking_issues'])
" 2>/dev/null

echo
echo "[12] 被引用的声音不允许删除"
CODE=$(curl -s -o "$TMP/d.json" -w '%{http_code}' -X DELETE "$BASE/api/v1/speakers/elysia")
check "删除被引用声音" "$CODE" "400"
python3 -c "
import json;d=json.load(open('$TMP/d.json'))
print('  提示:', d['message'][:70])
" 2>/dev/null

echo
echo "[13] 重新质量检测"
CODE=$(curl -s -o "$TMP/v.json" -w '%{http_code}' -X POST "$BASE/api/v1/speakers/elysia/validate")
check "POST /speakers/elysia/validate" "$CODE" "200"
python3 -c "
import json;d=json.load(open('$TMP/v.json'))
print('  ', d['summary'], '| SNR', d['snr_db'], 'dB | 静音比例', d['silence_ratio'])
" 2>/dev/null

echo
echo "[14] 改参考文本 -> 缓存 key 变化"
BEFORE=$(curl -s "$BASE/api/v1/speakers/elysia" | python3 -c "import json,sys;print(json.load(sys.stdin)['feature_cache_key'])")
# 先读出原始文本，改完必须恢复成它。
# 历史教训：早期版本这里硬编码了「官方示例」的文本，
# 把数据库里的正确参考文本覆盖掉，导致后续真模型合成在卷积层报
# "Kernel size can't be greater than actual input size"。
ORIG_TXT=$(curl -s "$BASE/api/v1/speakers/elysia" | python3 -c "import json,sys;print(json.load(sys.stdin)['transcript'])")
curl -s -X PATCH "$BASE/api/v1/speakers/elysia" -H 'Content-Type: application/json' \
  -d '{"transcript":"临时改动的文本用于验证缓存失效"}' > /dev/null
AFTER=$(curl -s "$BASE/api/v1/speakers/elysia" | python3 -c "import json,sys;print(json.load(sys.stdin)['feature_cache_key'])")
if [ "$BEFORE" != "$AFTER" ]; then ok "缓存 key 已失效 ($BEFORE -> $AFTER)"; else bad "缓存 key 未变"; fi
# 恢复为改之前读到的值，绝不硬编码
python3 - "$BASE" "$ORIG_TXT" <<'PY'
import json, sys, urllib.request
base, orig = sys.argv[1], sys.argv[2]
body = json.dumps({"transcript": orig}).encode()
req = urllib.request.Request(
    f"{base}/api/v1/speakers/elysia", data=body,
    headers={"Content-Type": "application/json"}, method="PATCH")
urllib.request.urlopen(req, timeout=30).read()
now = json.load(urllib.request.urlopen(f"{base}/api/v1/speakers/elysia", timeout=30))
assert now["transcript"] == orig, "恢复失败"
PY
if [ $? -eq 0 ]; then ok "参考文本已正确恢复"; else bad "参考文本恢复失败"; fi

echo
echo "[15] 删除角色"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$BASE/api/v1/characters/test_char")
check "DELETE /characters/test_char" "$CODE" "204"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/characters/test_char")
check "删除后 GET" "$CODE" "404"

echo
echo "[16] 清理测试数据"
curl -s -X DELETE "$BASE/api/v1/characters/patch_test" > /dev/null
curl -s -X DELETE "$BASE/api/v1/characters/bind_ghost" > /dev/null 2>&1

echo
echo "=============================================="
echo " 结果: $PASS 通过, $FAIL 失败"
echo "=============================================="
[ "$FAIL" -eq 0 ] || exit 1
