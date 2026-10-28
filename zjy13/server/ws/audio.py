"""上行音频缓冲与格式转换。

对应 V2 第五节与硬件核实的结论：

开发板采集格式（ESP32-S3-Touch-LCD-1.85B，官方 BSP 确认）：
    ES7210 输出 4 通道交错 int16，布局 "RMNM"
        通道 0 = Reference（扬声器回采，用于回声消除）
        通道 1 = Mic1
        通道 2 = 未明确（可能是第三个麦克风或噪声通道）
        通道 3 = Mic2

ASR 需要：16 kHz / 16 bit / 单声道。

因此服务端必须做三件事：
1. **按通道索引提取人声**，不能简单取平均（参考通道是回声，会污染结果）
2. **降混为单声道**
3. **重采样到 16 kHz**

缓冲区必须有上限，网络中断时释放资源（V2 第十一节第 2 条）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from ..protocol import MAX_TURN_AUDIO_BYTES, ProtocolError, ErrorCode

logger = logging.getLogger("ws.audio")

#: 开发板 ES7210 原始通道数
DEVICE_CHANNELS = 4

#: 各通道语义（来自官方示例 wake_word_drv.c 的 "RMNM" 布局）
CHANNEL_REFERENCE = 0
CHANNEL_MIC1 = 1
CHANNEL_UNKNOWN = 2
CHANNEL_MIC2 = 3

#: 用于合成人声的通道
VOICE_CHANNELS = (CHANNEL_MIC1, CHANNEL_MIC2, CHANNEL_UNKNOWN)


@dataclass(slots=True)
class AudioFormat:
    """音频流格式描述。"""

    sample_rate: int = 16000
    channels: int = DEVICE_CHANNELS
    bits: int = 16
    encoding: str = "pcm_s16le"
    channel_layout: str = "RMNM"

    @property
    def bytes_per_frame(self) -> int:
        return self.channels * (self.bits // 8)

    def to_dict(self) -> dict:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bits": self.bits,
            "encoding": self.encoding,
            "channel_layout": self.channel_layout,
        }

    def describe(self) -> str:
        return (
            f"{self.sample_rate} Hz / {self.bits} bit / {self.channels} ch "
            f"/ {self.encoding} / {self.channel_layout}"
        )


@dataclass(slots=True)
class TurnAudio:
    """一轮录音的缓冲，记录格式与统计。"""

    format: AudioFormat
    chunks: int = 0
    total_bytes: int = 0
    expected_seq: int = 0
    out_of_order: int = 0
    duplicates: int = 0
    #: 已收到的块数（含乱序），用于判断是否为首块
    received: int = 0
    # slots 类必须在字段里声明，不能动态挂属性
    _parts: list[bytes] = field(default_factory=list)
    _seqs: list[int] = field(default_factory=list)
    _seen: set[int] = field(default_factory=set)

    def append(self, seq: int, payload: bytes) -> None:
        """追加一个音频块。

        处理乱序与重复（V2 第十一节第 1 条）。
        策略：按 seq 排序后拼接，重复块丢弃。

        乱序计数只统计"收到了比预期小的 seq"，
        首块 seq 不为 0 不算乱序（发送端可能从任意值开始）。
        """
        if seq in self._seen:
            self.duplicates += 1
            return
        self._seen.add(seq)
        self._parts.append(payload)
        self._seqs.append(seq)
        self.chunks += 1
        self.total_bytes += len(payload)

        if self.received > 0 and seq < self.expected_seq:
            self.out_of_order += 1
        self.received += 1
        self.expected_seq = max(self.expected_seq, seq + 1)

        if self.total_bytes > MAX_TURN_AUDIO_BYTES:
            raise ProtocolError(
                ErrorCode.AUDIO_BUFFER_OVERFLOW,
                f"单轮音频超过上限 {MAX_TURN_AUDIO_BYTES} 字节，已缓冲 "
                f"{self.total_bytes}",
            )

    def raw_bytes(self) -> bytes:
        """按 seq 顺序拼接原始字节（处理乱序）。"""
        if not self._parts:
            return b""
        order = sorted(range(len(self._parts)), key=lambda i: self._seqs[i])
        return b"".join(self._parts[i] for i in order)

    def to_mono_16k(self, *, target_sr: int = 16000) -> np.ndarray:
        """转换成 ASR 需要的 16 kHz 单声道 float32 数组。

        步骤：
        1. 按声道解交错
        2. 提取人声通道（丢弃 reference）
        3. 降混为单声道
        4. 重采样到目标采样率
        """
        raw = self.raw_bytes()
        if not raw:
            return np.zeros(0, dtype=np.float32)

        fmt = self.format
        frame_bytes = fmt.bytes_per_frame
        if frame_bytes <= 0:
            raise ProtocolError(ErrorCode.BAD_MESSAGE, "非法音频格式")

        usable = len(raw) - (len(raw) % frame_bytes)
        if usable == 0:
            return np.zeros(0, dtype=np.float32)

        data = np.frombuffer(raw[:usable], dtype="<i2").astype(np.float32) / 32768.0
        frames = data.reshape(-1, fmt.channels)

        # 提取人声通道
        usable_ch = [c for c in VOICE_CHANNELS if c < fmt.channels]
        if not usable_ch:
            usable_ch = list(range(fmt.channels))
        mono = frames[:, usable_ch].mean(axis=1)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "音频转换: %s → %d 帧, 使用通道 %s",
                fmt.describe(), mono.size, usable_ch,
            )

        if fmt.sample_rate != target_sr and mono.size:
            mono = _resample_linear(mono, fmt.sample_rate, target_sr)
        return mono


def _resample_linear(data: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """线性插值重采样（与 asr/audio_utils.py 保持一致）。"""
    if src_sr == dst_sr or data.size == 0:
        return data
    duration = data.size / src_sr
    n_out = int(round(duration * dst_sr))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.arange(data.size, dtype=np.float64)
    x_new = np.linspace(0, data.size - 1, n_out, dtype=np.float64)
    return np.interp(x_new, x_old, data).astype(np.float32)


def encode_pcm16_wav(mono: np.ndarray, sample_rate: int) -> bytes:
    """把 float32 单声道写成 16-bit PCM WAV 字节（供 ASR 引擎使用）。"""
    import io
    import wave

    clipped = np.clip(mono, -1.0, 1.0)
    ints = np.round(clipped * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(ints.tobytes())
    return buf.getvalue()


__all__ = [
    "AudioFormat",
    "TurnAudio",
    "DEVICE_CHANNELS",
    "CHANNEL_REFERENCE",
    "CHANNEL_MIC1",
    "CHANNEL_MIC2",
    "VOICE_CHANNELS",
    "encode_pcm16_wav",
]