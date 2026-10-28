"""WebSocket 消息协议定义。

对应 V2 第十一节。客户端（ESP32 固件或模拟器）与服务端共用本定义。

设计要点：
1. **控制消息用 JSON，音频用二进制帧** —— V2 明确要求"不要把每个音频块
   都编码成 JSON 字符串传输"。16kHz PCM 每秒 32 KB，Base64 后涨到 43 KB。
2. **每条消息带 protocol_version** —— 固件烧录后难改，版本不匹配要能检出。
3. **三层标识**：session_id（会话）/ turn_id（轮次）/ seq（块序号）。
   过期响应靠 turn_id 丢弃，乱序靠 seq 重排。
4. **二进制帧头固定 12 字节**，便于 ESP32 端用 struct 解析。

二进制音频帧格式（小端）：

    偏移  长度  字段
    0     1     magic = 0xA5        帧类型标识
    1     1     msg_type            见 BinaryType
    2     2     seq                 块序号（uint16，回绕）
    4     4     turn_id_short       轮次短 ID（uint32）
    8     4     payload_len         负载长度（uint32）
    12    N     payload             PCM 数据

    总长 = 12 + payload_len
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

# ---------------------------------------------------------------------------
# 协议版本
# ---------------------------------------------------------------------------

PROTOCOL_VERSION = 1

# 二进制帧魔数，用于区分音频帧与其它数据
BINARY_MAGIC = 0xA5

# 二进制帧头长度
BINARY_HEADER_SIZE = 12

# ---------------------------------------------------------------------------
# 消息大小限制
# ---------------------------------------------------------------------------

#: 单条 JSON 控制消息最大字节数
MAX_JSON_BYTES = 64 * 1024

#: 单个音频块最大负载字节数。
#:
#: 取值依据（实测计算）：
#:   16kHz / 1ch / 100ms =  3200 B
#:   16kHz / 4ch /  60ms =  7680 B   ← 开发板典型配置
#:   48kHz / 4ch / 100ms = 38400 B   ← 最坏情况
#:
#: 定 48 KB 是为了覆盖 48kHz 4 通道 100ms 的极端配置。
#: 但要意识到：单块越大，ESP32 侧的内存压力越大。
#: **推荐开发板按 20-60ms 分块**（8 KB 以内），既控制延迟又省内存。
MAX_AUDIO_CHUNK_BYTES = 48 * 1024

#: 推荐的音频块时长（毫秒）。开发板应优先按此时长分块。
#: 取值理由：20ms 是语音处理常用帧长；100ms 延迟偏大，
#: 且单块内存占用随采样率与通道数线性增长。
RECOMMENDED_CHUNK_MS = (20, 40, 60)

#: 服务端单轮音频缓冲上限（约 60 秒 16k 单声道）
MAX_TURN_AUDIO_BYTES = 60 * 16000 * 2

#: 单轮文本长度上限
MAX_TEXT_CHARS = 1000

# ---------------------------------------------------------------------------
# 消息类型
# ---------------------------------------------------------------------------


class MsgType(str):
    """JSON 控制消息的 type 字段取值。"""


# 用模块级常量而非 Enum，方便直接比较字符串
DEVICE_HELLO = "device_hello"
SERVER_HELLO = "server_hello"
SESSION_START = "session_start"
RECORDING_START = "recording_start"
RECORDING_STOP = "recording_stop"
ASR_PARTIAL = "asr_partial"
ASR_FINAL = "asr_final"
LLM_START = "llm_start"
LLM_DELTA = "llm_delta"
LLM_FINAL = "llm_final"
TTS_START = "tts_start"
PLAYBACK_START = "playback_start"
PLAYBACK_DONE = "playback_done"
TURN_DONE = "turn_done"
CANCEL = "cancel"
TURN_CANCELLED = "turn_cancelled"
ERROR = "error"
HEARTBEAT = "heartbeat"
HEARTBEAT_ACK = "heartbeat_ack"

#: V2 第十一节列出的全部类型（tts_audio 走二进制帧，此处只留占位说明）
CONTROL_TYPES: frozenset[str] = frozenset({
    DEVICE_HELLO, SERVER_HELLO, SESSION_START, RECORDING_START, RECORDING_STOP,
    ASR_PARTIAL, ASR_FINAL, LLM_START, LLM_DELTA, LLM_FINAL, TTS_START,
    PLAYBACK_START, PLAYBACK_DONE, TURN_DONE, CANCEL, TURN_CANCELLED,
    ERROR, HEARTBEAT, HEARTBEAT_ACK,
})


class BinaryType(IntEnum):
    """二进制帧的 msg_type 取值。"""

    AUDIO_UP = 0x01      # 开发板 → 电脑：录音数据
    AUDIO_DOWN = 0x02    # 电脑 → 开发板：TTS 音频


# ---------------------------------------------------------------------------
# 错误码
# ---------------------------------------------------------------------------


class ErrorCode:
    """可读错误码。固件端可据此决定是否重试。"""

    PROTOCOL_VERSION_MISMATCH = "protocol_version_mismatch"
    BAD_MESSAGE = "bad_message"
    NOT_HELLO = "not_hello"
    UNKNOWN_TYPE = "unknown_type"
    NO_SUCH_TURN = "no_such_turn"
    TURN_BUSY = "turn_busy"
    AUDIO_TOO_LARGE = "audio_too_large"
    AUDIO_BUFFER_OVERFLOW = "audio_buffer_overflow"
    TEXT_TOO_LONG = "text_too_long"
    ASR_FAILED = "asr_failed"
    ASR_EMPTY = "asr_empty"
    LLM_FAILED = "llm_failed"
    TTS_FAILED = "tts_failed"
    TTS_UNAVAILABLE = "tts_unavailable"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


#: 可重试的错误码（客户端可稍后重试）
RETRYABLE_ERRORS: frozenset[str] = frozenset({
    ErrorCode.TURN_BUSY,
    ErrorCode.LLM_FAILED,
    ErrorCode.TTS_FAILED,
    ErrorCode.TTS_UNAVAILABLE,
    ErrorCode.INTERNAL,
})

# ---------------------------------------------------------------------------
# 消息构造
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Envelope:
    """所有 JSON 控制消息的公共信封。

    服务端出站消息建议统一带上 session_id / turn_id / seq，
    便于客户端丢弃过期响应。
    """

    type: str
    protocol_version: int = PROTOCOL_VERSION
    session_id: str = ""
    turn_id: str = ""
    seq: int = 0
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        msg: dict[str, Any] = {
            "type": self.type,
            "protocol_version": self.protocol_version,
        }
        if self.session_id:
            msg["session_id"] = self.session_id
        if self.turn_id:
            msg["turn_id"] = self.turn_id
        if self.seq:
            msg["seq"] = self.seq
        msg.update(self.payload)
        return msg


def make_error(
    code: str,
    message: str,
    *,
    session_id: str = "",
    turn_id: str = "",
    fatal: bool = False,
) -> dict[str, Any]:
    """构造 error 消息。

    :param fatal: 为 True 时客户端应断开重连（协议不匹配等）
    """
    return Envelope(
        type=ERROR,
        session_id=session_id,
        turn_id=turn_id,
        payload={
            "code": code,
            "message": message,
            "retryable": code in RETRYABLE_ERRORS,
            "fatal": fatal,
        },
    ).to_dict()


def make_hello_ack(
    *,
    server_name: str,
    audio_in_sample_rate: int,
    audio_in_channels: int,
    audio_out_sample_rate: int,
) -> dict[str, Any]:
    """服务端握手应答，**告知开发板期望的音频格式**。

    开发板据此配置采集与播放，避免双方格式不一致。
    """
    return Envelope(
        type=SERVER_HELLO,
        payload={
            "server_name": server_name,
            "audio_in": {
                "sample_rate": audio_in_sample_rate,
                "channels": audio_in_channels,
                "bits": 16,
                "encoding": "pcm_s16le",
            },
            "audio_out": {
                "sample_rate": audio_out_sample_rate,
                "channels": 1,
                "bits": 16,
                "encoding": "pcm_s16le",
            },
            "limits": {
                "max_audio_chunk_bytes": MAX_AUDIO_CHUNK_BYTES,
                "max_json_bytes": MAX_JSON_BYTES,
                "max_text_chars": MAX_TEXT_CHARS,
                "recommended_chunk_ms": list(RECOMMENDED_CHUNK_MS),
            },
        },
    ).to_dict()


# ---------------------------------------------------------------------------
# 二进制帧编解码
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AudioFrame:
    """一个音频二进制帧。"""

    msg_type: int
    seq: int
    turn_id_short: int
    payload: bytes

    def encode(self) -> bytes:
        if len(self.payload) > MAX_AUDIO_CHUNK_BYTES:
            raise ValueError(
                f"音频块过大: {len(self.payload)} > {MAX_AUDIO_CHUNK_BYTES}"
            )
        header = struct.pack(
            "<BBHII",
            BINARY_MAGIC,
            self.msg_type & 0xFF,
            self.seq & 0xFFFF,
            self.turn_id_short & 0xFFFFFFFF,
            len(self.payload),
        )
        return header + self.payload

    @classmethod
    def decode(cls, data: bytes) -> "AudioFrame":
        """解析二进制帧。格式非法时抛 ValueError。"""
        if len(data) < BINARY_HEADER_SIZE:
            raise ValueError(
                f"帧长度不足: {len(data)} < {BINARY_HEADER_SIZE}"
            )
        magic, msg_type, seq, turn_short, plen = struct.unpack(
            "<BBHII", data[:BINARY_HEADER_SIZE]
        )
        if magic != BINARY_MAGIC:
            raise ValueError(f"魔数不匹配: 0x{magic:02X} != 0x{BINARY_MAGIC:02X}")
        if msg_type not in (BinaryType.AUDIO_UP, BinaryType.AUDIO_DOWN):
            raise ValueError(f"未知二进制类型: {msg_type}")
        if plen > MAX_AUDIO_CHUNK_BYTES:
            raise ValueError(f"负载声明过大: {plen} > {MAX_AUDIO_CHUNK_BYTES}")
        body = data[BINARY_HEADER_SIZE:]
        if len(body) < plen:
            raise ValueError(f"负载不完整: 实际 {len(body)} < 声明 {plen}")
        return cls(
            msg_type=msg_type,
            seq=seq,
            turn_id_short=turn_short,
            payload=body[:plen],
        )


def turn_id_to_short(turn_id: str) -> int:
    """把字符串 turn_id 压缩成 32 位整数，供二进制帧头使用。

    用稳定的哈希（CRC32），保证同一 turn_id 在两端得到相同结果。
    仅用于帧头快速匹配；完整标识仍以 JSON 消息里的 turn_id 为准。
    """
    import zlib

    return zlib.crc32(turn_id.encode("utf-8")) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------


class ProtocolError(ValueError):
    """协议层错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_inbound(msg: dict[str, Any]) -> str:
    """校验入站 JSON 消息，返回 type。

    只做协议层校验，不涉及业务状态。
    """
    if not isinstance(msg, dict):
        raise ProtocolError(ErrorCode.BAD_MESSAGE, "消息必须是 JSON 对象")

    mtype = msg.get("type")
    if not isinstance(mtype, str) or not mtype:
        raise ProtocolError(ErrorCode.BAD_MESSAGE, "缺少 type 字段")

    if mtype not in CONTROL_TYPES:
        raise ProtocolError(ErrorCode.UNKNOWN_TYPE, f"未知消息类型: {mtype}")

    ver = msg.get("protocol_version")
    if ver is None:
        raise ProtocolError(ErrorCode.BAD_MESSAGE, "缺少 protocol_version")
    if int(ver) != PROTOCOL_VERSION:
        raise ProtocolError(
            ErrorCode.PROTOCOL_VERSION_MISMATCH,
            f"协议版本不匹配: 客户端 {ver}，服务端 {PROTOCOL_VERSION}",
        )
    return mtype


def validate_text(text: Any) -> str:
    """校验文本长度。"""
    if not isinstance(text, str):
        raise ProtocolError(ErrorCode.BAD_MESSAGE, "text 必须是字符串")
    if len(text) > MAX_TEXT_CHARS:
        raise ProtocolError(
            ErrorCode.TEXT_TOO_LONG,
            f"文本过长: {len(text)} > {MAX_TEXT_CHARS}",
        )
    return text


__all__ = [
    "PROTOCOL_VERSION",
    "BINARY_MAGIC",
    "BINARY_HEADER_SIZE",
    "MAX_JSON_BYTES",
    "MAX_AUDIO_CHUNK_BYTES",
    "RECOMMENDED_CHUNK_MS",
    "MAX_TURN_AUDIO_BYTES",
    "MAX_TEXT_CHARS",
    "CONTROL_TYPES",
    "BinaryType",
    "ErrorCode",
    "RETRYABLE_ERRORS",
    "Envelope",
    "AudioFrame",
    "ProtocolError",
    "make_error",
    "make_hello_ack",
    "turn_id_to_short",
    "validate_inbound",
    "validate_text",
    # 消息类型常量
    "DEVICE_HELLO", "SERVER_HELLO", "SESSION_START", "RECORDING_START",
    "RECORDING_STOP", "ASR_PARTIAL", "ASR_FINAL", "LLM_START", "LLM_DELTA",
    "LLM_FINAL", "TTS_START", "PLAYBACK_START", "PLAYBACK_DONE", "TURN_DONE",
    "CANCEL", "TURN_CANCELLED", "ERROR", "HEARTBEAT", "HEARTBEAT_ACK",
]