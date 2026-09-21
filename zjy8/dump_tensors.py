#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打印 GGUF 的 tensor_info（张量清单）并检查文件是否完整。

采用流式读取，只读文件头 / KV 段 / tensor_info 段，不会把数 GB 的张量数据读进内存。

注意：tensor_info 里的 offset 是 **相对于 tensor_data 段起点** 的相对偏移，
所以文件里张量数据的绝对位置 = data_offset + offset。

用法:
    python dump_tensors.py                       # 自动使用本目录下唯一的 .gguf
    python dump_tensors.py -i model.gguf
    python dump_tensors.py -i model.gguf --list  # 列出全部张量
"""

import argparse
import sys
from pathlib import Path

from gguf_meta import GgufError, read_gguf_info

SCRIPT_DIR = Path(__file__).resolve().parent


def dump_tensors(path: Path, list_all: bool = False) -> int:
    info = read_gguf_info(path)

    print(f"File: {info.path}")
    print(f"File size: {info.file_size}")
    print(f"Version: {info.version}, Tensors: {info.tensor_count}, KVs: {info.kv_count}")
    print(f"Alignment: {info.alignment}")
    print(f"KV section ends at: {info.tensor_info_end - len(info.tensor_info_bytes)}")
    print(f"Tensor info ends at: {info.tensor_info_end}")
    print(f"Data section starts at: {info.data_offset}")
    print(f"Data section size: {info.tensor_data_size}")

    if not info.tensors:
        print("（没有张量）")
        return 0

    if list_all:
        print()
        for i, t in enumerate(info.tensors):
            print(f"  [{i}] {t.name} dims={t.dims} type={t.tensor_type} offset={t.offset}")

    # offset 最大的张量（近似最后一块数据）
    last = max(info.tensors, key=lambda t: t.offset)
    required_size = info.data_offset + last.offset + 1

    print()
    print(f"Last tensor by offset: {last.name}")
    print(f"  dims: {last.dims}")
    print(f"  type: {last.tensor_type}")
    print(f"  offset from data start: {last.offset}")
    print(f"  would need file size >=: {required_size}")

    if info.file_size >= required_size:
        print("✅ 文件大小足够，tensor_info 与文件尺寸一致")
        return 0
    print("❌ 文件可能被截断：张量数据超出文件末尾")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="打印 GGUF 的 tensor_info 并检查文件完整性（流式读取）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python dump_tensors.py
    python dump_tensors.py -i model.gguf --list
        """,
    )
    parser.add_argument("-i", "--input", default=None, help="GGUF 文件路径（默认取本目录下唯一的 .gguf）")
    parser.add_argument("--list", action="store_true", help="列出全部张量（默认只打印最后一个）")
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
        return dump_tensors(path, args.list)
    except GgufError as e:
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
