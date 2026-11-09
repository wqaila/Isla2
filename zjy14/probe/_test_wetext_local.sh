#!/usr/bin/env bash
set -uo pipefail
source "${HOME}/cosyvoice-venv/bin/activate"

CACHE="${HOME}/.cache/modelscope/hub/pengzhendong/wetext"
echo "===== 检查 zh 目录完整性 ====="
ls -la "$CACHE/zh/tn/" 2>&1 | head -10
echo
ls -la "$CACHE/zh/itn/" 2>&1 | head -10

echo
echo "===== 用本地路径直接初始化（绕过 snapshot_download） ====="
python - <<PY
from kaldifst import TextNormalizer
from pathlib import Path

cache = Path("$CACHE")
zh_tagger = cache / "zh" / "tn" / "tagger.fst"
zh_verb = cache / "zh" / "tn" / "verbalizer.fst"
print(f"tagger  存在: {zh_tagger.is_file()}")
print(f"verb 存在: {zh_verb.is_file()}")

try:
    tagger = TextNormalizer(str(zh_tagger))
    verb = TextNormalizer(str(zh_verb))
    print("TextNormalizer 加载成功")
    tests = [
        "今天是2026年11月9日，温度25度。",
        "这个价格是100%，涨了3.14倍。",
        "他说：AI时代来了！",
    ]
    for t in tests:
        try:
            tagged = tagger(t)
            out = verb(tagged)
            print(f"  输入: {t}")
            print(f"  TN  : {out}")
        except Exception as exc:
            print(f"  处理失败 {t}: {type(exc).__name__}: {exc}")
except Exception as exc:
    print(f"加载失败: {type(exc).__name__}: {exc}")
PY
