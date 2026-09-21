#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
向 GGUF 文件补写缺失的 KV 项（默认补 tokenizer.ggml.model = "gpt2"）。

======================================================================
为什么 tensor offset 不需要修改（重要结论，勿改错！）
======================================================================
GGUF 文件布局：

    [文件头 24 字节][KV 段][tensor_info 段][对齐填充][tensor_data 段]

tensor_info 里每个张量的 offset 是 **相对于 tensor_data 段起点** 的相对偏移，
llama.cpp 读取时按 (数据段起点 + offset) 定位数据；数据段起点 =
align_up(tensor_info 结束位置, general.alignment)（默认 32 字节）。

所以：往 KV 段插入字节后，KV 段 / tensor_info 段 / 数据段只是整体后移，
「相对数据段起点」的 offset 保持不变，**绝对不能给 offset 再加 new_kv_size**。
（历史教训：本目录里曾有两个脚本给所有 offset 都加了 new_kv_size，
 结果是每个张量都向后错位 44 字节，模型直接被写坏。）

唯一需要重算的是 tensor_info 与 tensor_data 之间的**对齐填充**：
必须按新的 tensor_info 结束位置重新补零，保证数据段起点仍然对齐。

用法:
    python fix_gguf_final.py                          # 自动使用本目录下唯一的 .gguf
    python fix_gguf_final.py -i model.gguf -o fixed.gguf
"""

import argparse
import shutil
import struct
import sys
from pathlib import Path

from gguf_meta import GgufError, align_up, read_gguf_info

# 要补写的 KV（tokenizer.ggml.model = "gpt2"，GGUF 值类型 8 = STRING）
NEW_KEY = "tokenizer.ggml.model"
NEW_VALUE = "gpt2"
VALUE_TYPE_STRING = 8

# 复制张量数据时的块大小（8MB），避免把整文件读进内存
COPY_CHUNK_SIZE = 8 * 1024 * 1024

SCRIPT_DIR = Path(__file__).resolve().parent


def build_kv_bytes(key: str, value: str) -> bytes:
    """按 GGUF 格式序列化一个字符串类型的 KV 项"""
    key_bytes = key.encode("utf-8")
    value_bytes = value.encode("utf-8")
    return (
        struct.pack("<Q", len(key_bytes))
        + key_bytes
        + struct.pack("<I", VALUE_TYPE_STRING)
        + struct.pack("<Q", len(value_bytes))
        + value_bytes
    )


def find_default_gguf() -> Path | None:
    """在本脚本所在目录里找 .gguf；只有一个才自动使用，多个则返回 None 由调用方提示"""
    candidates = sorted(p for p in SCRIPT_DIR.glob("*.gguf") if p.is_file())
    if len(candidates) == 1:
        return candidates[0]
    return None


def list_local_gguf() -> list[Path]:
    return sorted(p for p in SCRIPT_DIR.glob("*.gguf") if p.is_file())


def fix_gguf(src: Path, out: Path) -> int:
    """把缺失的 KV 补进 GGUF，返回进程退出码"""
    info = read_gguf_info(src)

    print(f"📦 输入文件: {info.path}")
    print(f"   版本: v{info.version}  张量数: {info.tensor_count}  KV 数: {info.kv_count}")
    print(f"   对齐字节: {info.alignment}  张量数据段起点: {info.data_offset}")
    print(f"   文件大小: {info.file_size} 字节 ({info.file_size / 1024 ** 3:.2f} GB)")

    if info.has_kv(NEW_KEY):
        print(f"ℹ️  已存在 KV「{NEW_KEY}」，无需修复，未写出任何文件。")
        return 0

    if out.resolve() == info.path.resolve():
        print("❌ 输出文件不能与输入文件相同（避免写坏原始模型），请用 --output 指定另一个路径。")
        return 1

    new_kv = build_kv_bytes(NEW_KEY, NEW_VALUE)
    # 只改文件头里的 kv_count，其余字段（magic/version/tensor_count）原样保留
    new_header = info.header_bytes[:16] + struct.pack("<Q", info.kv_count + 1)

    with open(info.path, "rb") as fin, open(out, "wb") as fout:
        fout.write(new_header)
        fout.write(info.kv_bytes)
        fout.write(new_kv)
        # tensor_info 原样写出：offset 是相对数据段的，插入 KV 后无需修改
        fout.write(info.tensor_info_bytes)
        # 按新的 tensor_info 结束位置重算对齐填充
        padding = align_up(fout.tell(), info.alignment) - fout.tell()
        if padding:
            fout.write(b"\x00" * padding)
        # 流式复制张量数据段，不整体载入内存
        fin.seek(info.data_offset)
        shutil.copyfileobj(fin, fout, COPY_CHUNK_SIZE)

    # 自检：重新解析输出文件，确认 KV 已写入且所有张量 offset 与原来完全一致
    fixed = read_gguf_info(out)
    if fixed.kv_count != info.kv_count + 1:
        print("❌ 自检失败：输出文件的 KV 数量不正确，请勿使用该文件。")
        return 1
    if fixed.data_offset % fixed.alignment != 0:
        print("❌ 自检失败：输出文件的张量数据段未对齐，请勿使用该文件。")
        return 1
    if [(t.name, t.offset) for t in fixed.tensors] != [(t.name, t.offset) for t in info.tensors]:
        print("❌ 自检失败：输出文件的张量 offset 发生了变化，请勿使用该文件。")
        return 1

    print(f"✅ 已补写 KV: {NEW_KEY} = \"{NEW_VALUE}\"")
    print(f"📄 输出文件: {out}")
    print(f"   文件大小: {fixed.file_size} 字节 ({fixed.file_size / 1024 ** 3:.2f} GB)")
    print("   张量 offset 未改动，数据段重新对齐完毕。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="向 GGUF 补写缺失的 KV 项（默认 tokenizer.ggml.model = gpt2）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python fix_gguf_final.py
    python fix_gguf_final.py -i gemma-4-E2B-it-Q5_K_S.gguf -o gemma-fixed.gguf

说明:
    输入文件不会就地修改，输出默认写到 <输入文件名>-fixed.gguf。
    插入 KV 后张量 offset 无需改动，只会重算数据段前的对齐填充。
        """,
    )
    parser.add_argument("-i", "--input", default=None, help="输入的 .gguf 文件（默认取本目录下唯一的 .gguf）")
    parser.add_argument("-o", "--output", default=None, help="输出的 .gguf 文件（默认 <输入名>-fixed.gguf）")
    args = parser.parse_args()

    if args.input:
        src = Path(args.input).expanduser()
        if not src.is_file():
            print(f"❌ 找不到输入文件: {src}")
            return 1
    else:
        src = find_default_gguf()
        if src is None:
            local = list_local_gguf()
            print(f"❌ {SCRIPT_DIR} 下没有唯一的 .gguf 文件，请用 --input 指定。")
            if local:
                print("   当前目录下的 .gguf:")
                for p in local:
                    print(f"   - {p.name}")
            return 1

    out = Path(args.output).expanduser() if args.output else src.with_name(f"{src.stem}-fixed{src.suffix}")

    try:
        return fix_gguf(src, out)
    except GgufError as e:
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
