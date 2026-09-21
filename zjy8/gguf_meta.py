#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GGUF 元信息读取工具（流式，不会把整个模型读进内存）

只读取「文件头 + KV 段 + tensor_info 段」，张量数据段只记录偏移、不读入内存，
因此可以安全地处理数 GB 的 GGUF 模型文件。

======================================================================
GGUF 文件布局与 offset 语义（重要结论，勿改错！）
======================================================================
    [文件头 24 字节][KV 段][tensor_info 段][对齐填充][tensor_data 段]

  * 文件头: magic("GGUF") + version(u32) + tensor_count(u64) + kv_count(u64)
  * tensor_info 里每个张量的 offset 是 **相对于 tensor_data 段起点** 的
    相对偏移，llama.cpp 读取时按 (数据段起点 + offset) 定位张量数据。
  * 数据段起点 = align_up(tensor_info 结束位置, general.alignment)，
    对齐值默认 32 字节（general.alignment 这个 KV 可以覆盖它）。

  推论：
    1. 往 KV 段插入 / 删除字节，只会让后面的所有段落整体平移，
       **张量的 offset 完全不需要修改**（因为它本来就是相对的）。
       给 offset 再加/减插入的字节数会让所有张量错位，直接写坏模型！
    2. 唯一需要重算的是 tensor_info 与 tensor_data 之间的**对齐填充**：
       必须按新的 tensor_info 结束位置重新补零，才能保证数据段起点仍是对齐的。
"""

import struct
from pathlib import Path

# GGUF 文件魔数
GGUF_MAGIC = b"GGUF"
# 文件头固定长度：magic(4) + version(4) + tensor_count(8) + kv_count(8)
GGUF_HEADER_SIZE = 24
# 默认对齐字节数（对应 KV: general.alignment）
DEFAULT_ALIGNMENT = 32

# GGUF 值类型编号 -> 固定字节数（STRING / ARRAY 单独处理）
_FIXED_VALUE_SIZES = {
    0: 1,   # UINT8
    1: 1,   # INT8
    2: 2,   # UINT16
    3: 2,   # INT16
    4: 4,   # UINT32
    5: 4,   # INT32
    6: 4,   # FLOAT32
    7: 1,   # BOOL
    10: 8,  # UINT64
    11: 8,  # INT64
    12: 8,  # FLOAT64
}

# 值类型编号 -> 名称（仅用于打印）
VALUE_TYPE_NAMES = {
    0: "UINT8",
    1: "INT8",
    2: "UINT16",
    3: "INT16",
    4: "UINT32",
    5: "INT32",
    6: "FLOAT32",
    7: "BOOL",
    8: "STRING",
    9: "ARRAY",
    10: "UINT64",
    11: "INT64",
    12: "FLOAT64",
}

# 数组元素最多收集多少个值用于展示（更长的数组只打印长度，避免占用内存）
_ARRAY_PREVIEW_LIMIT = 16


class GgufError(Exception):
    """GGUF 文件读取相关的错误"""


def align_up(value: int, alignment: int) -> int:
    """把 value 向上对齐到 alignment 的整数倍"""
    if alignment <= 0:
        return value
    return (value + alignment - 1) // alignment * alignment


def _read_exact(f, size: int, what: str = "数据") -> bytes:
    """精确读取 size 字节，不足则报错（避免静默读到截断的文件）"""
    buf = f.read(size)
    if len(buf) != size:
        raise GgufError(
            f"读取{what}失败：文件可能被截断（期望 {size} 字节，实际只有 {len(buf)} 字节）"
        )
    return buf


def _read_u32(f) -> int:
    return struct.unpack("<I", _read_exact(f, 4))[0]


def _read_u64(f) -> int:
    return struct.unpack("<Q", _read_exact(f, 8))[0]


def _read_string(f) -> str:
    size = _read_u64(f)
    return _read_exact(f, size, "字符串").decode("utf-8", errors="replace")


class ArrayValue:
    """GGUF 数组类型的值（values 只保留前若干个元素，长数组为 None）"""

    __slots__ = ("element_type", "length", "values")

    def __init__(self, element_type: int, length: int, values):
        self.element_type = element_type
        self.length = length
        self.values = values

    @property
    def element_type_name(self) -> str:
        return VALUE_TYPE_NAMES.get(self.element_type, f"type={self.element_type}")


def _read_value(f, value_type: int):
    """
    读取一个 GGUF 值，返回 (值, 占用的字节数)。

    数组返回 ArrayValue；长数组只按长度跳过，不会把全部元素读进内存。
    """
    if value_type in _FIXED_VALUE_SIZES:
        size = _FIXED_VALUE_SIZES[value_type]
        raw = _read_exact(f, size)
        if value_type == 4:
            return struct.unpack("<I", raw)[0], size
        if value_type == 5:
            return struct.unpack("<i", raw)[0], size
        if value_type == 6:
            return struct.unpack("<f", raw)[0], size
        if value_type == 10:
            return struct.unpack("<Q", raw)[0], size
        if value_type == 11:
            return struct.unpack("<q", raw)[0], size
        if value_type == 12:
            return struct.unpack("<d", raw)[0], size
        return raw[0], size

    if value_type == 8:  # STRING
        size = _read_u64(f)
        text = _read_exact(f, size, "字符串").decode("utf-8", errors="replace")
        return text, 8 + size

    if value_type == 9:  # ARRAY
        array_type = _read_u32(f)
        array_len = _read_u64(f)
        # 定长元素的大数组：直接 seek 跳过，不读入内存
        if array_type in _FIXED_VALUE_SIZES and array_len > _ARRAY_PREVIEW_LIMIT:
            total = array_len * _FIXED_VALUE_SIZES[array_type]
            f.seek(total, 1)
            return ArrayValue(array_type, array_len, None), 12 + total
        values = []
        total = 12
        for _ in range(array_len):
            value, size = _read_value(f, array_type)
            total += size
            values.append(value)
        return ArrayValue(array_type, array_len, values), total

    raise GgufError(f"未知的 GGUF 值类型编号: {value_type}")


class KvEntry:
    """GGUF 里的一个键值对"""

    __slots__ = ("key", "value_type", "value")

    def __init__(self, key: str, value_type: int, value):
        self.key = key
        self.value_type = value_type
        self.value = value

    @property
    def type_name(self) -> str:
        return VALUE_TYPE_NAMES.get(self.value_type, f"type={self.value_type}")


class TensorInfo:
    """GGUF 里的一个张量描述"""

    __slots__ = ("name", "dims", "tensor_type", "offset")

    def __init__(self, name: str, dims, tensor_type: int, offset: int):
        self.name = name
        self.dims = dims
        self.tensor_type = tensor_type
        # offset 是相对于 tensor_data 段起点的相对偏移
        self.offset = offset


class GgufInfo:
    """GGUF 文件的元信息（不含张量数据）"""

    def __init__(
        self,
        path: Path,
        version: int,
        tensor_count: int,
        kv_count: int,
        alignment: int,
        kv_entries,
        tensors,
        header_bytes: bytes,
        kv_bytes: bytes,
        tensor_info_bytes: bytes,
        tensor_info_end: int,
        data_offset: int,
        file_size: int,
    ):
        self.path = path
        self.version = version
        self.tensor_count = tensor_count
        self.kv_count = kv_count
        self.alignment = alignment
        self.kv_entries = kv_entries
        self.tensors = tensors
        self.header_bytes = header_bytes
        self.kv_bytes = kv_bytes
        self.tensor_info_bytes = tensor_info_bytes
        self.tensor_info_end = tensor_info_end
        # 张量数据段在文件中的起始位置（已对齐）
        self.data_offset = data_offset
        self.file_size = file_size

    def find_kv(self, key: str):
        """按键名查找 KV，找不到返回 None"""
        for entry in self.kv_entries:
            if entry.key == key:
                return entry
        return None

    def has_kv(self, key: str) -> bool:
        return self.find_kv(key) is not None

    @property
    def tensor_data_size(self) -> int:
        """张量数据段的字节数"""
        return self.file_size - self.data_offset


def read_gguf_info(path) -> GgufInfo:
    """
    流式读取 GGUF 的元信息。

    Args:
        path: GGUF 文件路径

    Returns:
        GgufInfo 对象

    Raises:
        GgufError: 文件不存在、不是 GGUF、版本不支持或文件被截断
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise GgufError(f"找不到 GGUF 文件: {file_path}")

    with open(file_path, "rb") as f:
        header_bytes = _read_exact(f, GGUF_HEADER_SIZE, "文件头")
        if header_bytes[:4] != GGUF_MAGIC:
            raise GgufError(
                f"不是 GGUF 文件（文件头为 {header_bytes[:4]!r}，期望 {GGUF_MAGIC!r}）: {file_path}"
            )

        version = struct.unpack_from("<I", header_bytes, 4)[0]
        tensor_count = struct.unpack_from("<Q", header_bytes, 8)[0]
        kv_count = struct.unpack_from("<Q", header_bytes, 16)[0]
        if version < 2:
            # v1 的 tensor offset 是 32 位，本工具不处理
            raise GgufError(f"只支持 GGUF v2/v3，当前文件版本为 v{version}")

        # KV 段：顺序读取，值按类型跳过/读取
        kv_start = f.tell()
        kv_entries = []
        for _ in range(kv_count):
            key = _read_string(f)
            value_type = _read_u32(f)
            value, _ = _read_value(f, value_type)
            kv_entries.append(KvEntry(key, value_type, value))
        kv_end = f.tell()

        f.seek(kv_start)
        kv_bytes = _read_exact(f, kv_end - kv_start, "KV 段")

        # tensor_info 段：原样读出来备用（体积很小）
        tensor_info_start = f.tell()
        tensors = []
        for _ in range(tensor_count):
            name = _read_string(f)
            n_dims = _read_u32(f)
            dims = [_read_u64(f) for _ in range(n_dims)]
            tensor_type = _read_u32(f)
            offset = _read_u64(f)
            tensors.append(TensorInfo(name, dims, tensor_type, offset))
        tensor_info_end = f.tell()

        f.seek(tensor_info_start)
        tensor_info_bytes = _read_exact(f, tensor_info_end - tensor_info_start, "tensor_info 段")

    # 对齐值可以被 general.alignment 覆盖
    alignment = DEFAULT_ALIGNMENT
    for entry in kv_entries:
        if entry.key == "general.alignment" and isinstance(entry.value, int) and entry.value > 0:
            alignment = entry.value
            break

    data_offset = align_up(tensor_info_end, alignment)
    file_size = file_path.stat().st_size
    if data_offset > file_size:
        raise GgufError(
            f"文件被截断：张量数据段应从 {data_offset} 字节处开始，但文件只有 {file_size} 字节"
        )

    return GgufInfo(
        path=file_path,
        version=version,
        tensor_count=tensor_count,
        kv_count=kv_count,
        alignment=alignment,
        kv_entries=kv_entries,
        tensors=tensors,
        header_bytes=header_bytes,
        kv_bytes=kv_bytes,
        tensor_info_bytes=tensor_info_bytes,
        tensor_info_end=tensor_info_end,
        data_offset=data_offset,
        file_size=file_size,
    )
