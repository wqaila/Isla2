"""修复 PowerShell 的 .Replace 把 \\f 当转义符造成的源码损坏。

损坏模式（都发生在行首或特定位置）：
  rrom  -> from
  der   -> def
  selr  -> self
  __ruture__ -> __future__

只修这 4 个文件，逐个精确替换。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path("/mnt/d/xiangmudaima/zjy14")
TARGETS = [
    "custom_voice/config/schema.py",
    "custom_voice/expression/instruction.py",
    "custom_voice/server/routes/speakers.py",
    "custom_voice/server/routes/synthesis.py",
]

# 按顺序应用：先修更具体的
FIXES: list[tuple[str, str]] = [
    (r"\brrom\b", "from"),
    (r"\bder\b", "def"),
    (r"\bselr\b", "self"),
    (r"__ruture__", "__future__"),
]


def main() -> int:
    total = 0
    for rel in TARGETS:
        p = ROOT / rel
        text = p.read_text(encoding="utf-8")
        original = text
        for pat, repl in FIXES:
            text = re.sub(pat, repl, text)
        if text != original:
            p.write_text(text, encoding="utf-8")
            # 统计修复次数
            changed = sum(
                len(re.findall(pat, original)) for pat, _ in FIXES
            )
            total += changed
            print(f"  修复 {rel}（{changed} 处）")
        else:
            print(f"  无需修复 {rel}")

    print(f"\n共修复 {total} 处")

    # 验证
    print("\n验证语法...")
    import compileall

    ok = compileall.compile_dir(str(ROOT / "custom_voice"), quiet=1, force=True)
    print("语法检查通过" if ok else "仍有语法错误")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
