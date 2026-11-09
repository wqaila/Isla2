#!/usr/bin/env bash
# 验证 Phase 1 的硬性约束：D10 安全守门 + 配置消费登记
set -uo pipefail
cd /mnt/d/xiangmudaima/zjy14 || exit 1
# shellcheck disable=SC1091
source "${HOME}/cosyvoice-venv/bin/activate"
export PYTHONPATH=/mnt/d/xiangmudaima/zjy14

echo "=============================================="
echo " 硬性约束验证"
echo "=============================================="

echo
echo "[D10-1] host=0.0.0.0 且无 auth_token —— 必须拒绝启动"
OUT=$(ZJY14_HOST=0.0.0.0 ZJY14_AUTH_TOKEN="" timeout 30 python -m custom_voice.server 2>&1)
RC=$?
if echo "$OUT" | grep -q "auth_token"; then
    echo "  [PASS] 已拒绝启动，并提示 auth_token"
    echo "         提示原文: $(echo "$OUT" | grep 'auth_token' | head -1 | cut -c1-100)"
else
    echo "  [FAIL] 未拒绝启动 (rc=$RC)"
    echo "$OUT" | head -5
fi

echo
echo "[D10-2] host=0.0.0.0 且带 token —— 应允许启动"
OUT=$(ZJY14_HOST=0.0.0.0 ZJY14_AUTH_TOKEN=demo timeout 6 python -m custom_voice.server 2>&1 || true)
if echo "$OUT" | grep -q "对外开放监听"; then
    echo "  [PASS] 允许启动并给出安全提示"
else
    echo "  [FAIL] 未按预期启动"
    echo "$OUT" | head -5
fi

echo
echo "[CFG-1] 未知字段 —— 必须报错"
printf 'engine: stub\nbogus_field: 1\n' > /tmp/bad_unknown.yaml
python - <<'PY'
from pathlib import Path
from custom_voice.config.loader import load_config
try:
    load_config(Path("/tmp/bad_unknown.yaml"))
    print("  [FAIL] 未报错")
except Exception as e:
    first = str(e).splitlines()[0]
    ok = "bogus_field" in str(e)
    print(f"  [{'PASS' if ok else 'FAIL'}] {first[:100]}")
PY

echo
echo "[CFG-2] 值越界 —— 必须报错并指出字段"
printf 'max_concurrent_requests: 0\n' > /tmp/bad_range.yaml
python - <<'PY'
from pathlib import Path
from custom_voice.config.loader import load_config
try:
    load_config(Path("/tmp/bad_range.yaml"))
    print("  [FAIL] 未报错")
except Exception as e:
    ok = "max_concurrent_requests" in str(e)
    print(f"  [{'PASS' if ok else 'FAIL'}] 已指出越界字段")

PY

echo
echo "[CFG-3] 未登记消费方的字段 —— 必须报错"
python - <<'PY'
import custom_voice.config.schema as s
from custom_voice.config.loader import load_config, validate_consumed
cfg = load_config()
orig = dict(s.CONSUMED_FIELDS)
try:
    del s.CONSUMED_FIELDS["speaker_cache_max_items"]
    try:
        validate_consumed(cfg)
        print("  [FAIL] 未检出未消费字段")
    except Exception as e:
        ok = "speaker_cache_max_items" in str(e)
        print(f"  [{'PASS' if ok else 'FAIL'}] 已检出并指出字段名")
finally:
    s.CONSUMED_FIELDS.clear()
    s.CONSUMED_FIELDS.update(orig)
PY

echo
echo "[CFG-4] 登记表与模型字段完全一致"
python - <<'PY'
from custom_voice.config.schema import CONSUMED_FIELDS, GPUConfig
a = set(CONSUMED_FIELDS)
b = set(GPUConfig.model_fields)
if a == b:
    print(f"  [PASS] 完全一致（{len(a)} 个字段）")
else:
    print(f"  [FAIL] 差异: 多登记={a-b}, 未登记={b-a}")
PY

echo
echo "[ARCH-1] server/ 与 schemas/ 不得 import 模型依赖"
python - <<'PY'
import ast
from pathlib import Path
root = Path("custom_voice")
bad = []
for sub in ("server", "schemas"):
    for f in (root / sub).rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                if m.split(".")[0] in ("cosyvoice", "torch", "onnxruntime", "transformers"):
                    bad.append(f"{f}: {m}")
print(f"  [{'PASS' if not bad else 'FAIL'}] 分层约束" + ("" if not bad else f" 违规: {bad}"))
PY

echo
echo "=============================================="
