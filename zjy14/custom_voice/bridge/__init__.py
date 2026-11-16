"""bridge 包：与 zjy13 的设备协议对接。

不重复实现 WebSocket / 固件 / 会话管理 —— 那些在 zjy13 已验证。
本包只负责「zjy14 引擎输出」→「zjy13 协议帧」的适配。
"""

from .protocol import (
    BINARY_HEADER_SIZE,
    BINARY_MAGIC,
    BYTES_PER_SEC,
    DEFAULT_FRAME_BYTES,
    FIRST_BURST_MS,
    BackpressureState,
    FrameStats,
    decode_binary_frame,
    describe_compat,
    encode_binary_frame,
    encode_length_prefixed,
    encode_stream_end,
    iter_frames,
)
from .stream import (
    FakeDevice,
    StreamResult,
    stream_to_http_format,
    stream_to_protocol_frames,
)

__all__ = [
    "BINARY_HEADER_SIZE",
    "BINARY_MAGIC",
    "BYTES_PER_SEC",
    "DEFAULT_FRAME_BYTES",
    "FIRST_BURST_MS",
    "BackpressureState",
    "FakeDevice",
    "FrameStats",
    "StreamResult",
    "decode_binary_frame",
    "describe_compat",
    "encode_binary_frame",
    "encode_length_prefixed",
    "encode_stream_end",
    "iter_frames",
    "stream_to_http_format",
    "stream_to_protocol_frames",
]
