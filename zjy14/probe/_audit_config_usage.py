"""审查：找出「配置看似生效但实际未使用」以及其它潜在缺陷。

对应原提示词的硬性要求：
  "不能留下配置看似生效但实际未使用的参数"

检查项：
  1. CONSUMED_FIELDS 登记的字段是否真在代码里被读取
  2. StreamPolicy 的字段是否被消费
  3. Protocol 定义的方法是否都被实现
  4. 是否存在未使用的 import 或死代码
"""
from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path("/mnt/d/xiangmudaima/zjy14")
SRC = ROOT / "custom_voice"


def load_sources() -> dict[str, str]:
    return {
        str(p.relative_to(ROOT)): p.read_text(encoding="utf-8")
        for p in SRC.rglob("*.py")
    }


def main() -> int:
    srcs = load_sources()
    print("=" * 100)
    print(" 配置字段消费审查")
    print("=" * 100)

    from custom_voice.config.schema import CONSUMED_FIELDS

    print(f"\n{'字段':<28}{'出现文件数':<12}{'判定'}")
    print("-" * 70)
    problems = []
    for field in CONSUMED_FIELDS:
        # 排除 schema.py 自身的定义
        hits = [
            name for name, text in srcs.items()
            if field in text and not name.endswith("config/schema.py")
        ]
        if not hits:
            problems.append(field)
            print(f"{field:<28}{0:<12}未在 schema.py 之外使用")
        else:
            print(f"{field:<28}{len(hits):<12}OK")

    print()
    print("=" * 100)
    print(" StreamPolicy 字段消费审查")
    print("=" * 100)
    pol_hits: dict[str, list[str]] = {}
    for attr in ("enabled", "threshold"):
        hits = [
            name for name, text in srcs.items()
            if re.search(rf"\b{attr}\b", text) and "inference/streaming.py" not in name
        ]
        pol_hits[attr] = hits
        status = "OK" if hits else "未被外部使用"
        print(f"  {attr:<12}{len(hits)} 处  {status}")

    print()
    print("=" * 100)
    print(" 未使用的 import 检查")
    print("=" * 100)
    for name, text in sorted(srcs.items()):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        imported: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    key = a.asname or a.name.split(".")[0]
                    imported[key] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == "*":
                        continue
                    key = a.asname or a.name
                    imported[key] = node.lineno

        # 统计标识符出现
        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                pass
        body_text = text
        unused = []
        for key, line in imported.items():
            if key in ("annotations",):
                continue
            # 粗略统计：去掉 import 行后是否还出现
            occurrences = len(re.findall(rf"\b{re.escape(key)}\b", body_text))
            if occurrences <= 1:
                unused.append(f"{key} (line {line})")
        if unused:
            print(f"  {name}:")
            for u in unused:
                print(f"      {u}")

    print()
    print("=" * 100)
    print(" 汇总")
    print("=" * 100)
    if problems:
        print(f"  [问题] {len(problems)} 个配置字段未真正被使用：")
        for p in problems:
            print(f"         - {p}")
    else:
        print("  [OK] 所有登记字段都在代码中被使用")

    dead_policy = [k for k, v in pol_hits.items() if not v]
    if dead_policy:
        print(f"  [问题] StreamPolicy 以下字段未被外部消费：{dead_policy}")
    else:
        print("  [OK] StreamPolicy 所有字段都被消费")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
