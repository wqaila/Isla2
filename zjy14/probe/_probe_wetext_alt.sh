#!/usr/bin/env bash
set -uo pipefail
source "${HOME}/cosyvoice-venv/bin/activate"

echo "===== 1. kaldifst 是否可用（wetext 的硬依赖） ====="
python - <<'PY'
try:
    import kaldifst
    print(f"  kaldifst OK  {getattr(kaldifst, '__version__', '?')}")
    from kaldifst import TextNormalizer
    print("  TextNormalizer 可导入")
except Exception as exc:
    print(f"  kaldifst 问题: {type(exc).__name__}: {exc}")
PY

echo
echo "===== 2. 尝试从 HuggingFace 找 wetext 资源 ====="
python - <<'PY'
import urllib.request, json
urls = [
    "https://huggingface.co/api/models/pengzhendong/wetext",
    "https://huggingface.co/pengzhendong/wetext/resolve/main/README.md",
]
for u in urls:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read(600)
            print(f"  [200] {u}")
            print(f"        {data[:200]!r}")
    except Exception as exc:
        print(f"  [{type(exc).__name__}] {u}: {exc}")
PY

echo
echo "===== 3. modelscope 直连尝试（不带 token） ====="
python - <<'PY'
import urllib.request
urls = [
    "https://www.modelscope.cn/api/v1/models/pengzhendong/wetext/repo/files",
    "https://modelscope.cn/api/v1/models/pengzhendong/wetext",
]
for u in urls:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"  [200] {u}  -> {r.read(300)!r}")
    except Exception as exc:
        print(f"  [{type(exc).__name__}] {u}: {exc}")
PY

echo
echo "===== 4. 是否已有缓存的 wetext 目录 ====="
for d in "${HOME}/.cache/modelscope/hub/pengzhendong/wetext" \
         "${HOME}/.cache/modelscope/hub/models/pengzhendong/wetext"; do
  if [ -d "$d" ]; then
    echo "  存在: $d"
    find "$d" -type f | head -20
  else
    echo "  不存在: $d"
  fi
done
