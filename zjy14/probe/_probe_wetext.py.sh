#!/usr/bin/env bash
set -uo pipefail
source "${HOME}/cosyvoice-venv/bin/activate"

echo "===== wetext 是什么、需要什么 ====="
python - <<'PY'
import wetext, inspect, os
print("wetext 路径:", os.path.dirname(inspect.getfile(wetext)))
try:
    from wetext import Normalizer
    print("\n尝试无参初始化 zh Normalizer:")
    try:
        n = Normalizer(remove_erhua=False)
        print("  成功!")
        print("  测试:", n.normalize("今天是2026年11月9日，温度25度"))
    except Exception as exc:
        print(f"  失败: {type(exc).__name__}: {exc}")
except Exception as exc:
    print(f"导入失败: {exc}")
PY

echo
echo "===== wetext 包里有什么 ====="
python - <<'PY'
import wetext, inspect, os
d = os.path.dirname(inspect.getfile(wetext))
for root, dirs, files in os.walk(d):
    dirs[:] = [x for x in dirs if x != "__pycache__"]
    level = root.replace(d, "").count(os.sep)
    indent = "  " * level
    print(f"{indent}{os.path.basename(root)}/")
    for f in sorted(files)[:12]:
        size = os.path.getsize(os.path.join(root, f))
        print(f"{indent}  {f}  ({size} bytes)")
PY

echo
echo "===== 尝试离线初始化（不下载） ====="
python - <<'PY'
import os
os.environ["MODELSCOPE_OFFLINE"] = "1"
try:
    from wetext import Normalizer
    n = Normalizer(remove_erhua=False)
    print("离线模式初始化成功")
    print("测试:", n.normalize("今天是2026年11月9日"))
except Exception as exc:
    print(f"离线失败: {type(exc).__name__}: {exc}")
PY
