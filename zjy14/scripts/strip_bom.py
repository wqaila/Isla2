"""去掉文件开头的 UTF-8 BOM。PowerShell 的 Set-Content -Encoding UTF8 会写入 BOM。"""

from __future__ import annotations

import sys
from pathlib import Path

BOM = b"\xef\xbb\xbf"


def strip_bom(path: Path) -> bool:
    raw = path.read_bytes()
    if raw.startswith(BOM):
        path.write_bytes(raw[len(BOM):])
        return True
    return False


def main(argv: list[str]) -> int:
    targets = [Path(p) for p in argv[1:]] or list(
        Path("custom_voice").rglob("*.py")
    ) + list(Path("tests").rglob("*.py"))
    fixed = 0
    for p in targets:
        if p.is_file() and strip_bom(p):
            print(f"去除 BOM: {p}")
            fixed += 1
    print(f"共处理 {fixed} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
