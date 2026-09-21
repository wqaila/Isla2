#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查 / 打印 GGUF 模型的元信息（文件头 + KV 段 + 张量概况）。

采用流式读取，只读文件头与 KV 段，不会把数 GB 的模型整体读进内存。

用法:
    python check_model.py                          # 自动使用本目录下唯一的 .gguf
    python check_model.py -i model-Q8_0.gguf
    python check_model.py -i model.gguf --all      # 打印所有 KV 的完整值
"""

import argparse
import io
import sys
from pathlib import Path

from gguf_meta import GgufError, read_gguf_info

# Windows 控制台默认不是 UTF-8，强制用 UTF-8 输出，避免特殊字符报错
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 超过这个长度的字符串值只打印长度（除非 --all）
MAX_INLINE_LEN = 300

SCRIPT_DIR = Path(__file__).resolve().parent


def should_print_full(key: str, text: str, show_all: bool) -> bool:
    """判断某个字符串 KV 是否完整打印（沿用原脚本的取舍：chat_template/general 等始终完整打印）"""
    if show_all:
        return True
    if "chat_template" in key or "general" in key or "token" not in key:
        return True
    return len(text) < MAX_INLINE_LEN


def print_kv(entry, index: int, show_all: bool) -> None:
    """按类型打印一个 KV 项"""
    key = entry.key
    value = entry.value

    if entry.value_type == 8:  # STRING
        text = value
        if should_print_full(key, text, show_all):
            if len(text) > MAX_INLINE_LEN and not show_all:
                text = text[:MAX_INLINE_LEN] + "..."
            print(f'  KV[{index}] {key} (STRING) = "{text}"')
        else:
            print(f"  KV[{index}] {key} (STRING) = [len={len(text)}]")

    elif entry.value_type == 9:  # ARRAY
        array = value
        print(
            f"  KV[{index}] {key} (ARRAY type={array.element_type_name}, len={array.length})"
        )
        if array.values is None:
            print(f"    ... ({array.length} 项，已跳过不读入内存)")
        else:
            for j, item in enumerate(array.values):
                text = repr(item)
                if len(text) > 80:
                    text = text[:80] + "..."
                print(f"    [{j}] = {text}")

    elif entry.value_type in (4, 5, 6, 10, 11, 12):  # 数值
        print(f"  KV[{index}] {key} ({entry.type_name}) = {value}")

    else:
        print(f"  KV[{index}] {key} ({entry.type_name}) = {value!r}")


def check_model(path: Path, show_all: bool = False) -> int:
    info = read_gguf_info(path)

    size_gb = info.file_size / 1024 ** 3
    print(f"File: {info.path}")
    print(f"File size: {info.file_size} bytes ({size_gb:.2f} GB)")
    print(f"Magic: {info.header_bytes[:4]!r}")
    print(f"Version: {info.version}")
    print(f"Tensors: {info.tensor_count}, KVs: {info.kv_count}")
    print(f"Alignment: {info.alignment}")
    print(f"KV section ends at: {info.tensor_info_end - len(info.tensor_info_bytes)}")
    print(f"Tensor info ends at: {info.tensor_info_end}")
    print(f"Tensor data starts at: {info.data_offset}")
    print()

    for i, entry in enumerate(info.kv_entries):
        print_kv(entry, i, show_all)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="检查 / 打印 GGUF 模型的元信息（流式读取，不占内存）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python check_model.py
    python check_model.py -i model-Q8_0.gguf
    python check_model.py -i model.gguf --all
        """,
    )
    parser.add_argument("-i", "--input", default=None, help="GGUF 文件路径（默认取本目录下唯一的 .gguf）")
    parser.add_argument("--all", action="store_true", help="打印所有 KV 的完整值（含超长 tokenizer 值）")
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
        return check_model(path, args.all)
    except GgufError as e:
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
