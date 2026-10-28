"""音频缓冲与格式转换测试。

覆盖硬件核实得出的关键结论：
- ES7210 是 4 通道，布局 RMNM
- **必须按通道提取人声，不能简单取平均**（参考通道是回声）
- 22050 Hz 需重采样到 16 kHz
- 乱序、重复、超限处理
"""

from __future__ import annotations

import numpy as np
import pytest

from server.protocol import ErrorCode, ProtocolError
from server.ws.audio import (
    CHANNEL_MIC1,
    CHANNEL_MIC2,
    CHANNEL_REFERENCE,
    DEVICE_CHANNELS,
    AudioFormat,
    TurnAudio,
    encode_pcm16_wav,
)


def make_interleaved(
    frames: int,
    *,
    ref: int = 0,
    mic1: int = 1000,
    unk: int = 0,
    mic2: int = 1200,
) -> bytes:
    """构造 4 通道交错 PCM 数据。"""
    a = np.full(frames, ref, dtype="<i2")
    b = np.full(frames, mic1, dtype="<i2")
    c = np.full(frames, unk, dtype="<i2")
    d = np.full(frames, mic2, dtype="<i2")
    return np.stack([a, b, c, d], axis=1).reshape(-1).tobytes()


@pytest.fixture()
def fmt() -> AudioFormat:
    return AudioFormat(sample_rate=16000, channels=4, channel_layout="RMNM")


# ---------------------------------------------------------------------------
# 格式描述
# ---------------------------------------------------------------------------


def test_format_bytes_per_frame(fmt: AudioFormat) -> None:
    assert fmt.bytes_per_frame == 4 * 2
    assert fmt.to_dict()["channel_layout"] == "RMNM"
    assert "RMNM" in fmt.describe()


def test_device_channels_is_4() -> None:
    """ES7210 输出 4 通道，这是硬件事实。"""
    assert DEVICE_CHANNELS == 4


# ---------------------------------------------------------------------------
# 通道提取（核心）
# ---------------------------------------------------------------------------


def test_reference_channel_excluded(fmt: AudioFormat) -> None:
    """核心用例：回声参考通道必须排除。

    如果错误地对 4 通道取平均，电平会被 reference 拉高 3.5 倍，
    送进 ASR 会得到噪声结果。
    """
    ta = TurnAudio(format=fmt)
    ta.append(0, make_interleaved(1000, ref=8000, mic1=1000, unk=0, mic2=1200))

    mono = ta.to_mono_16k()
    got = float(mono.mean())
    # 期望：仅 (mic1 + unk + mic2) / 3
    expect = (1000 + 0 + 1200) / 3 / 32768
    wrong = (8000 + 1000 + 0 + 1200) / 4 / 32768

    assert got == pytest.approx(expect, abs=1e-4)
    assert abs(got - wrong) > 0.01, "若等于 4 通道均值说明 reference 未被排除"


def test_mic1_and_mic2_both_used(fmt: AudioFormat) -> None:
    """两个麦克风都应参与，不能只用一路。"""
    ta1 = TurnAudio(format=fmt)
    ta1.append(0, make_interleaved(100, mic1=1000, mic2=0, unk=0))
    ta2 = TurnAudio(format=fmt)
    ta2.append(0, make_interleaved(100, mic1=0, mic2=1000, unk=0))

    lvl1 = float(ta1.to_mono_16k().mean())
    lvl2 = float(ta2.to_mono_16k().mean())
    assert lvl1 == pytest.approx(lvl2, abs=1e-6)
    assert lvl1 > 0


def test_single_channel_input_still_works() -> None:
    """兼容开发板直发单声道的情况。"""
    fmt = AudioFormat(sample_rate=16000, channels=1, channel_layout="M")
    ta = TurnAudio(format=fmt)
    data = np.full(800, 1000, dtype="<i2").tobytes()
    ta.append(0, data)
    mono = ta.to_mono_16k()
    assert mono.size == 800
    assert float(mono.mean()) == pytest.approx(1000 / 32768, abs=1e-4)


# ---------------------------------------------------------------------------
# 重采样
# ---------------------------------------------------------------------------


def test_resample_22050_to_16000() -> None:
    """开发板 BSP 默认 22050 Hz，需转为 ASR 的 16000 Hz。"""
    fmt = AudioFormat(sample_rate=22050, channels=4)
    ta = TurnAudio(format=fmt)
    ta.append(0, make_interleaved(22050))

    mono = ta.to_mono_16k()
    assert mono.size == pytest.approx(16000, abs=2)


def test_no_resample_when_already_16k() -> None:
    fmt = AudioFormat(sample_rate=16000, channels=4)
    ta = TurnAudio(format=fmt)
    ta.append(0, make_interleaved(16000))
    assert ta.to_mono_16k().size == 16000


def test_resample_48000_to_16000() -> None:
    fmt = AudioFormat(sample_rate=48000, channels=4)
    ta = TurnAudio(format=fmt)
    ta.append(0, make_interleaved(48000))
    assert ta.to_mono_16k().size == pytest.approx(16000, abs=2)


# ---------------------------------------------------------------------------
# 乱序与重复
# ---------------------------------------------------------------------------


def test_out_of_order_reassembled() -> None:
    """乱序到达的块必须按 seq 重排（V2 第十一节第 1 条）。"""
    fmt = AudioFormat(sample_rate=16000, channels=1)
    ta = TurnAudio(format=fmt)
    # 故意乱序：先发 seq=1，再发 seq=0
    ta.append(1, np.full(10, 200, dtype="<i2").tobytes())
    ta.append(0, np.full(10, 100, dtype="<i2").tobytes())

    raw = np.frombuffer(ta.raw_bytes(), dtype="<i2")
    assert raw[0] == 100, "seq=0 应在前面"
    assert raw[10] == 200, "seq=1 应在后面"
    assert ta.out_of_order == 1


def test_duplicate_chunk_dropped() -> None:
    fmt = AudioFormat(sample_rate=16000, channels=1)
    ta = TurnAudio(format=fmt)
    payload = np.full(10, 100, dtype="<i2").tobytes()
    ta.append(0, payload)
    ta.append(0, payload)  # 重复
    assert ta.chunks == 1
    assert ta.duplicates == 1
    assert len(ta.raw_bytes()) == len(payload)


def test_buffer_overflow_raises() -> None:
    """单轮音频超限必须抛错并释放，不能无限增长。"""
    from server.protocol import MAX_TURN_AUDIO_BYTES

    fmt = AudioFormat(sample_rate=16000, channels=1)
    ta = TurnAudio(format=fmt)
    big = b"\x00" * (1024 * 1024)
    with pytest.raises(ProtocolError) as e:
        for i in range(MAX_TURN_AUDIO_BYTES // len(big) + 5):
            ta.append(i, big)
    assert e.value.code == ErrorCode.AUDIO_BUFFER_OVERFLOW


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------


def test_empty_buffer(fmt: AudioFormat) -> None:
    ta = TurnAudio(format=fmt)
    assert ta.to_mono_16k().size == 0
    assert ta.raw_bytes() == b""


def test_partial_frame_ignored(fmt: AudioFormat) -> None:
    """不足一帧的尾部字节应被丢弃，不能错位。"""
    ta = TurnAudio(format=fmt)
    # 4 通道 x 2 字节 = 8 字节/帧；给 10 字节 = 1 帧 + 2 字节残余
    ta.append(0, make_interleaved(1) + b"\x01\x02")
    mono = ta.to_mono_16k()
    assert mono.size == 1


def test_wav_encoding_roundtrip() -> None:
    import io
    import wave

    mono = np.linspace(-0.5, 0.5, 1000, dtype=np.float32)
    wav = encode_pcm16_wav(mono, 16000)
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() == 1000


def test_wav_clips_out_of_range() -> None:
    """超出 [-1,1] 的值必须裁剪，不能溢出成噪声。"""
    import io
    import wave

    mono = np.array([2.0, -2.0], dtype=np.float32)
    wav = encode_pcm16_wav(mono, 16000)
    with wave.open(io.BytesIO(wav), "rb") as w:
        raw = np.frombuffer(w.readframes(2), dtype="<i2")
    assert raw[0] == 32767
    assert raw[1] == -32767