"""zjy13 协议桥接：把 zjy14 引擎的音频适配成设备端能消费的帧。

背景（决策 D2）：
  zjy14 是 zjy13 的引擎升级，不是平行项目。
  固件、WebSocket 协议、角色卡全部复用，zjy14 只替换 TTS 引擎。

  升级路径：
    ESP32 固件 → zjy13/server/ws/server.py → zjy13/server/tts/client.py
                                          → HTTP :50001 → zjy14 /synthesize_stream

  zjy13 侧唯一改动：TTS_BASE_URL 从 :50000 改到 :50001。

本模块负责「zjy14 引擎输出」→「zjy13 协议帧」的适配，不重复实现协议。

关键契约（从 zjy13 源码实测确认）：
  1. HTTP 流式路径：/synthesize_stream（两边一致，无需改）
  2. 流格式：4 字节大端 uint32 长度 + PCM16LE，长度 0 表示结束
  3. 响应头：x-sample-rate 必需（zjy13 TTSClient:405 读取）
  4. WS 音频帧：12 字节头（magic 0xA5 + type + seq + turn_id_short + len）
  5. 分帧大小：4096 字节 ≈ 128ms @ 24kHz 16bit（zjy13 ws/server.py:1085）
  6. 固件预缓冲：APP_PLAYBACK_RING_MS = 200ms
  7. 首块策略：首块至少发 300ms 音频，让板子攒满预缓冲立刻开播
     （zjy13 ws/server.py:1100 的 FIRST_BURST_MS）
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

#: zjy13 协议的二进制帧魔数
BINARY_MAGIC = 0xA5

#: 二进制帧头长度（字节）
BINARY_HEADER_SIZE = 12

#: 下行 TTS 音频的帧类型
BINARY_TYPE_TTS = 0x02

#: 单个音频块上限（zjy13 protocol MAX_AUDIO_CHUNK_BYTES）
MAX_AUDIO_CHUNK_BYTES = 48 * 1024

#: zjy13 服务端的分帧大小。4096 字节 ≈ 128ms @ 24kHz 16bit
DEFAULT_FRAME_BYTES = 4096

#: 固件播放环形缓冲时长（毫秒）
FIRMWARE_RING_MS = 200

#: 首块至少发送的音频时长（毫秒）。
#: 依据 zjy13 ws/server.py 的 FIRST_BURST_MS：固件要攒够预缓冲才开播，
#: 若首块太小，板子会一直等不满，表现为"更晚才出声"。
FIRST_BURST_MS = 300

#: 采样率（CosyVoice3 原生，与固件播放侧一致）
SAMPLE_RATE = 24000

#: 16bit 单声道每秒字节数
BYTES_PER_SEC = SAMPLE_RATE * 2


@dataclass(slots=True)
class FrameStats:
    """帧切分统计。"""

    frames: int = 0
    total_bytes: int = 0
    first_frame_bytes: int = 0
    max_frame_bytes: int = 0
    min_frame_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "frames": self.frames,
            "total_bytes": self.total_bytes,
            "audio_s": round(self.total_bytes / BYTES_PER_SEC, 3),
            "first_frame_bytes": self.first_frame_bytes,
            "max_frame_bytes": self.max_frame_bytes,
            "min_frame_bytes": self.min_frame_bytes,
        }


def encode_binary_frame(
    payload: bytes,
    seq: int,
    *,
    turn_id_short: int = 0,
    msg_type: int = BINARY_TYPE_TTS,
) -> bytes:
    """构造 zjy13 协议的二进制音频帧。

    帧格式（小端，固定 12 字节头，来自 zjy13 protocol/messages.py）：
        偏移  长度  字段
        0     1     magic = 0xA5
        1     1     msg_type（0x01 上行录音 / 0x02 下行 TTS）
        2     2     seq（uint16，回绕）
        4     4     turn_id_short（uint32）
        8     4     payload_len
        12    N     payload（PCM s16le 裸数据）

    :raises ValueError: payload 超过协议上限
    """
    if len(payload) > MAX_AUDIO_CHUNK_BYTES:
        raise ValueError(
            f"音频块过大：len(payload)={len(payload)} > {MAX_AUDIO_CHUNK_BYTES}（协议上限）"
        )
    if not (0 <= seq <= 0xFFFF):
        # uint16 回绕
        seq = seq & 0xFFFF

    header = struct.pack(
        "<BBHII",
        BINARY_MAGIC,
        msg_type,
        seq,
        turn_id_short & 0xFFFFFFFF,
        len(payload),
    )
    return header + payload


def decode_binary_frame(data: bytes) -> dict:
    """解析 zjy13 协议的二进制帧。用于测试与调试。"""
    if len(data) < BINARY_HEADER_SIZE:
        raise ValueError(f"帧过短：{len(data)} < {BINARY_HEADER_SIZE}")
    magic, msg_type, seq, turn_short, plen = struct.unpack(
        "<BBHII", data[:BINARY_HEADER_SIZE]
    )
    if magic != BINARY_MAGIC:
        raise ValueError(
            f"magic 不匹配：0x{magic:02X} != 0x{BINARY_MAGIC:02X}"
        )
    if len(data) < BINARY_HEADER_SIZE + plen:
        raise ValueError(
            f"负载不完整：声明 {plen} 字节，实际只有 "
            f"{len(data) - BINARY_HEADER_SIZE}"
        )
    return {
        "magic": magic,
        "msg_type": msg_type,
        "seq": seq,
        "turn_id_short": turn_short,
        "payload_len": plen,
        "payload": data[BINARY_HEADER_SIZE:BINARY_HEADER_SIZE + plen],
    }


def encode_length_prefixed(pcm: bytes) -> bytes:
    """zjy14 的 HTTP 流式分块格式（与 zjy13 完全一致）。

    每块 = 4 字节大端 uint32 长度 + PCM。
    """
    return struct.pack(">I", len(pcm)) + pcm


def encode_stream_end() -> bytes:
    """流式结束标记：长度 0 的块。"""
    return struct.pack(">I", 0)


def iter_frames(
    pcm_chunks,  # noqa: ANN001
    *,
    frame_bytes: int = DEFAULT_FRAME_BYTES,
    turn_id_short: int = 0,
) -> tuple[list[bytes], FrameStats]:
    """把若干 PCM 块切成协议帧。

    切分策略（对齐 zjy13 ws/server.py）：
      - 首帧至少 FIRST_BURST_MS 毫秒音频（让固件攒满预缓冲立刻开播）
      - 后续按 frame_bytes 固定切分
      - 余量并入最后一帧，不产生过小的尾帧

    :param pcm_chunks: PCM 字节块的迭代器/列表（来自引擎流式输出）
    :param frame_bytes: 每帧字节数，默认 4096（≈128ms）
    :param turn_id_short: 轮次短 ID，写入帧头
    :return: (帧列表, 统计)
    """
    if frame_bytes < 1:
        raise ValueError("frame_bytes 必须 >= 1")

    first_burst_bytes = BYTES_PER_SEC * FIRST_BURST_MS // 1000

    frames: list[bytes] = []
    stats = FrameStats()
    buf = bytearray()
    seq = 0

    def flush(size: int) -> None:
        nonlocal buf, seq
        while len(buf) >= size:
            piece = bytes(buf[:size])
            del buf[:size]
            frames.append(
                encode_binary_frame(
                    piece, seq, turn_id_short=turn_id_short
                )
            )
            if stats.frames == 0:
                stats.first_frame_bytes = len(piece)
            stats.frames += 1
            stats.total_bytes += len(piece)
            stats.max_frame_bytes = max(stats.max_frame_bytes, len(piece))
            stats.min_frame_bytes = (
                len(piece) if stats.min_frame_bytes == 0
                else min(stats.min_frame_bytes, len(piece))
            )
            seq += 1

    for chunk in pcm_chunks:
        if not chunk:
            continue
        buf.extend(chunk)
        # 首帧用较大的尺寸，之后用标准尺寸
        target = first_burst_bytes if stats.frames == 0 else frame_bytes
        flush(target)

    # 剩余数据：若存在则作为最后一帧（可能小于 frame_bytes）
    if buf:
        piece = bytes(buf)
        frames.append(
            encode_binary_frame(piece, seq, turn_id_short=turn_id_short)
        )
        if stats.frames == 0:
            stats.first_frame_bytes = len(piece)
        stats.frames += 1
        stats.total_bytes += len(piece)
        stats.max_frame_bytes = max(stats.max_frame_bytes, len(piece))
        stats.min_frame_bytes = (
            len(piece) if stats.min_frame_bytes == 0
            else min(stats.min_frame_bytes, len(piece))
        )

    return frames, stats


@dataclass(slots=True)
class BackpressureState:
    """背压状态：跟踪「已发送」与「已播放」的时间差。

    为什么需要（zjy13 ws/server.py:1180）：
      热点链路带宽有限，若服务端把整段音频一次性灌下去，
      会推爆 transport 导致写超时断连。
      所以要按播放进度节流，保持领先量不超过 lead_sec。
    """

    lead_sec: float = 0.8
    sent_bytes: int = 0
    first_burst_bytes: int = field(
        default_factory=lambda: BYTES_PER_SEC * FIRST_BURST_MS // 1000
    )

    def should_wait(self, played_sec: float, next_piece_bytes: int) -> float:
        """返回需要等待的秒数（0 表示不用等）。

        :param played_sec: 客户端已播放的秒数
        :param next_piece_bytes: 即将发送的字节数
        """
        # 首块阶段不节流
        if self.sent_bytes < self.first_burst_bytes:
            return 0.0

        sent_sec = (self.sent_bytes + next_piece_bytes) / BYTES_PER_SEC
        ahead = sent_sec - played_sec
        if ahead > self.lead_sec:
            return ahead - self.lead_sec
        return 0.0

    def mark_sent(self, nbytes: int) -> None:
        self.sent_bytes += nbytes

    def to_dict(self) -> dict:
        return {
            "lead_sec": self.lead_sec,
            "sent_bytes": self.sent_bytes,
            "sent_audio_s": round(self.sent_bytes / BYTES_PER_SEC, 3),
        }


def describe_compat() -> dict:
    """返回与 zjy13 的契约对照，供 /system/status 展示。

    这是「不重复造轮子」的证据：每一项都对应 zjy13 源码里的具体位置。
    """
    return {
        "protocol_version": 1,
        "binary_magic": f"0x{BINARY_MAGIC:02X}",
        "binary_header_size": BINARY_HEADER_SIZE,
        "frame_bytes": DEFAULT_FRAME_BYTES,
        "frame_ms": round(DEFAULT_FRAME_BYTES / BYTES_PER_SEC * 1000),
        "sample_rate": SAMPLE_RATE,
        "first_burst_ms": FIRST_BURST_MS,
        "firmware_ring_ms": FIRMWARE_RING_MS,
        "http_stream_path": "/synthesize_stream",
        "http_stream_format": "4-byte BE length prefix + PCM16LE, len=0 表示结束",
        "sources": {
            "binary_frame": "zjy13/server/protocol/messages.py",
            "frame_bytes": "zjy13/server/ws/server.py:1085",
            "first_burst": "zjy13/server/ws/server.py:1100",
            "ring_buffer": "zjy13/firmware/main/app_config.h:103",
            "stream_format": "zjy13/scripts/tts_server_wsl.py",
        },
    }


__all__ = [
    "BINARY_HEADER_SIZE",
    "BINARY_MAGIC",
    "BINARY_TYPE_TTS",
    "BYTES_PER_SEC",
    "DEFAULT_FRAME_BYTES",
    "FIRMWARE_RING_MS",
    "FIRST_BURST_MS",
    "MAX_AUDIO_CHUNK_BYTES",
    "SAMPLE_RATE",
    "BackpressureState",
    "FrameStats",
    "decode_binary_frame",
    "describe_compat",
    "encode_binary_frame",
    "encode_length_prefixed",
    "encode_stream_end",
    "iter_frames",
]
