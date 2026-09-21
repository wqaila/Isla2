#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验 GGUF 文件：打印 KV 段并检查关键字段是否齐全。

采用流式读取，只读文件头与 KV 段，不会把数 GB 的模型整体读进内存。

用法:
    python verify_gguf.py                        # 自动使用本目录下唯一的 .gguf
    python verify_gguf.py -i model-fixed.gguf
"""

import argparse
import sys
from pathlib import Path

from gguf_meta import GgufError, read_gguf_info

SCRIPT_DIR = Path(__file__).resolve().parent

# 一个可用的 GGUF 至少应包含这些 KV（缺了通常会导致加载失败或分词异常）
REQUIRED_KEYS = [
    "general.architecture",
    "general.name",
    "tokenizer.ggml.model",
]


def format_value(entry) -> str:
    """把 KV 值格式化成一行可读文本"""
    value = entry.value
    if entry.value_type == 8:  # STRING
        text = value if len(value) <= 120 else value[:120] + "..."
        return f'"{text}"'
    if entry.value_type == 9:  # ARRAY
        return f"[array of {value.length} ({value.element_type_name})]"
    return str(value)


def verify_gguf(path: Path) -> int:
    info = read_gguf_info(path)

    print(f"File: {info.path}")
    print(f"File size: {info.file_size} bytes ({info.file_size / 1024 ** 3:.2f} GB)")
    print(f"Version: {info.version}, Tensors: {info.tensor_count}, KVs: {info.kv_count}")
    print()

    for i, entry in enumerate(info.kv_entries):
        print(f"  KV[{i}] {entry.key} = {format_value(entry)}")

    print()
    missing = [key for key in REQUIRED_KEYS if not info.has_kv(key)]
    if missing:
        print(f"❌ 缺少关键 KV: {', '.join(missing)}")
        return 1

    print("✅ 关键 KV 齐全，文件头 / KV 段结构正常")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="校验 GGUF 文件并打印 KV 段（流式读取）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python verify_gguf.py
    python verify_gguf.py -i model-fixed.gguf
        """,
    )
    parser.add_argument("-i", "--input", default=None, help="GGUF 文件路径（默认取本目录下唯一的 .gguf）")
    args = parser.parse_args()

    if args.input:
        path = Path(args.input).expanduser()
    else:
        candidates = sorted(p for p in SCRIPT_DIR.glob("*.gguf") if p.is_file())
        if len(candidates) != 1:
            print(f"❌ {SCRIPT_DIR} 下没有唯一的 .gguf 文件，请用 --input 指定。")
            if candidates:
                print("   当前目录下的 .gguf:")
                for p in candidates:
                    print(f"   - {p.name}")
            return 1
        path = candidates[0]

    try:
        return verify_gguf(path)
    except GgufError as e:
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
