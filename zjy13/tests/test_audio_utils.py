"""音频工具测试：格式探测、重采样、规范化、异常处理。

这些测试只依赖 numpy 与标准库，不需要 torch/FunASR，可在任意环境运行。
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from asr.audio_utils import (
    MIN_DURATION_S,
    TARGET_SR,
    AudioFormatError,
    needs_conversion,
    normalize_to_asr_wav,
    probe_wav,
    read_pcm,
    resample_linear,
    write_pcm16_wav,
)


def make_wav(
    path: Path,
    *,
    sr: int = 16000,
    channels: int = 1,
    width: int = 2,
    seconds: float = 1.0,
    freq: float = 440.0,
) -> Path:
    """生成一个带正弦波的测试 WAV。"""
    n = int(sr * seconds)
    t = np.arange(n, dtype=np.float32) / sr
    mono = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    data = np.repeat(mono, channels) if channels > 1 else mono

    if width == 1:
        ints = np.round(data * 127 + 128).clip(0, 255).astype(np.uint8)
    elif width == 2:
        ints = np.round(data * 32767).astype("<i2")
    elif width == 4:
        ints = np.round(data * 2147483647).astype("<i4")
    else:
        raise ValueError("测试只支持 1/2/4 字节")

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(sr)
        w.writeframes(ints.tobytes())
    return path


# --------------------------------------------------------------------------
# 探测
# --------------------------------------------------------------------------


def test_probe_basic(tmp_path: Path) -> None:
    p = make_wav(tmp_path / "a.wav", sr=16000, seconds=1.5)
    info = probe_wav(p)
    assert info.sample_rate == 16000
    assert info.channels == 1
    assert info.bits_per_sample == 16
    assert info.duration_s == pytest.approx(1.5, abs=0.01)


def test_probe_missing_file(tmp_path: Path) -> None:
    with pytest.raises(AudioFormatError, match="不存在"):
        probe_wav(tmp_path / "nope.wav")


def test_probe_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.wav"
    p.write_bytes(b"")
    with pytest.raises(AudioFormatError, match="为空"):
        probe_wav(p)


def test_probe_not_wav(tmp_path: Path) -> None:
    p = tmp_path / "bad.wav"
    p.write_bytes(b"this is not a wav file at all")
    with pytest.raises(AudioFormatError):
        probe_wav(p)


def test_needs_conversion_flags(tmp_path: Path) -> None:
    ok = probe_wav(make_wav(tmp_path / "ok.wav", sr=16000, channels=1, width=2))
    assert needs_conversion(ok) is False

    bad_sr = probe_wav(make_wav(tmp_path / "sr.wav", sr=44100, channels=1, width=2))
    assert needs_conversion(bad_sr) is True

    bad_ch = probe_wav(make_wav(tmp_path / "ch.wav", sr=16000, channels=2, width=2))
    assert needs_conversion(bad_ch) is True


# --------------------------------------------------------------------------
# 读取与重采样
# --------------------------------------------------------------------------


def test_read_pcm_normalizes_range(tmp_path: Path) -> None:
    p = make_wav(tmp_path / "a.wav", seconds=0.5)
    data, sr = read_pcm(p)
    assert sr == 16000
    assert data.dtype == np.float32
    assert -1.0 <= float(data.min()) and float(data.max()) <= 1.0
    assert data.size == int(16000 * 0.5)


def test_read_pcm_downmix(tmp_path: Path) -> None:
    p = make_wav(tmp_path / "stereo.wav", channels=2, seconds=0.5)
    data, _ = read_pcm(p)
    # 立体声降混后长度应为帧数，不是采样总数
    assert data.size == int(16000 * 0.5)


def test_resample_changes_length() -> None:
    src = np.sin(np.linspace(0, 20, 44100)).astype(np.float32)
    out = resample_linear(src, 44100, 16000)
    assert abs(out.size - 16000) <= 2


def test_resample_same_rate_is_identity() -> None:
    src = np.arange(100, dtype=np.float32)
    out = resample_linear(src, 16000, 16000)
    assert np.array_equal(out, src)


def test_resample_empty() -> None:
    out = resample_linear(np.zeros(0, dtype=np.float32), 8000, 16000)
    assert out.size == 0


# --------------------------------------------------------------------------
# 规范化
# --------------------------------------------------------------------------


def test_normalize_already_correct(tmp_path: Path) -> None:
    src = make_wav(tmp_path / "src.wav", sr=16000, channels=1, width=2, seconds=1.0)
    res = normalize_to_asr_wav(src, tmp_path / "out")
    assert res.converted is False
    assert res.method == "无"
    assert res.target.sample_rate == TARGET_SR
    assert "无需转换" in res.summary()


def test_normalize_resamples_44k(tmp_path: Path) -> None:
    src = make_wav(tmp_path / "src44.wav", sr=44100, channels=1, width=2, seconds=1.0)
    res = normalize_to_asr_wav(src, tmp_path / "out")
    assert res.converted is True
    assert res.source.sample_rate == 44100
    assert res.target.sample_rate == TARGET_SR
    assert "重采样" in res.method
    # 时长应基本不变
    assert res.target.duration_s == pytest.approx(res.source.duration_s, abs=0.05)


def test_normalize_downmix_stereo(tmp_path: Path) -> None:
    src = make_wav(tmp_path / "st.wav", channels=2, seconds=1.0)
    res = normalize_to_asr_wav(src, tmp_path / "out")
    assert res.source.channels == 2
    assert res.target.channels == 1
    assert "声道" in res.method


def test_normalize_8bit(tmp_path: Path) -> None:
    src = make_wav(tmp_path / "8bit.wav", width=1, seconds=1.0)
    res = normalize_to_asr_wav(src, tmp_path / "out")
    assert res.source.bits_per_sample == 8
    assert res.target.bits_per_sample == 16
    assert "位宽" in res.method


def test_normalize_too_short(tmp_path: Path) -> None:
    src = make_wav(tmp_path / "short.wav", seconds=MIN_DURATION_S / 2)
    with pytest.raises(AudioFormatError, match="过短"):
        normalize_to_asr_wav(src, tmp_path / "out")


def test_normalize_silence_rejected(tmp_path: Path) -> None:
    """几乎静音的音频应被拒绝，避免把空录音送进模型。"""
    p = tmp_path / "silence.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.zeros(16000, dtype="<i2").tobytes())
    with pytest.raises(AudioFormatError, match="静音"):
        normalize_to_asr_wav(p, tmp_path / "out")


def test_normalize_records_both_formats(tmp_path: Path) -> None:
    """文档要求记录转换前后的格式。"""
    src = make_wav(tmp_path / "s.wav", sr=48000, channels=2, width=2, seconds=1.0)
    res = normalize_to_asr_wav(src, tmp_path / "out")
    d = res.to_dict()
    assert d["source"]["sample_rate"] == 48000
    assert d["source"]["channels"] == 2
    assert d["target"]["sample_rate"] == 16000
    assert d["target"]["channels"] == 1
    assert d["converted"] is True


def test_write_pcm16_roundtrip(tmp_path: Path) -> None:
    data = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
    p = write_pcm16_wav(data, 16000, tmp_path / "rt.wav")
    back, sr = read_pcm(p)
    assert sr == 16000
    assert back.size == data.size
    assert float(back[3]) == pytest.approx(1.0, abs=1e-3)
