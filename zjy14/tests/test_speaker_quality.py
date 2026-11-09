"""Phase 3 测试：音频转换、质量检测、仓储、缓存。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from custom_voice.audio.convert import (
    TARGET_SAMPLE_RATE,
    normalize_peak,
    probe,
    read_audio,
    resample,
    to_pcm16,
    write_wav,
)
from custom_voice.speaker import validate as quality


def _make_wav(
    path: Path,
    *,
    duration: float = 8.0,
    sr: int = 24000,
    freq: float = 220.0,
    amp: float = 0.5,
    channels: int = 1,
) -> Path:
    t = np.arange(int(duration * sr)) / sr
    data = np.sin(2 * np.pi * freq * t) * amp
    if channels > 1:
        data = np.stack([data] * channels, axis=1)
    sf.write(str(path), data.astype(np.float32), sr, subtype="PCM_16")
    return path


# ----------------------------------------------------------------------
# 音频转换
# ----------------------------------------------------------------------


def test_probe_reads_info(tmp_path: Path) -> None:
    p = _make_wav(tmp_path / "a.wav", duration=5.0)
    info = probe(p)
    assert info.sample_rate == 24000
    assert info.channels == 1
    assert info.duration_s == pytest.approx(5.0, abs=0.01)
    assert info.is_target_format


def test_read_audio_downmix(tmp_path: Path) -> None:
    p = _make_wav(tmp_path / "s.wav", duration=1.0, channels=2)
    samples, sr = read_audio(p)
    assert sr == 24000
    assert samples.ndim == 1


def test_resample_changes_length() -> None:
    samples = np.sin(np.linspace(0, 100, 16000)).astype(np.float32)
    out = resample(samples, 16000, 24000)
    assert abs(out.size - 24000) <= 2


def test_resample_same_rate_is_identity() -> None:
    samples = np.ones(1000, dtype=np.float32)
    out = resample(samples, 24000, 24000)
    assert out.size == 1000


def test_to_pcm16_range() -> None:
    samples = np.array([-1.0, 0.0, 1.0, 2.0, -2.0], dtype=np.float32)
    pcm = to_pcm16(samples)
    ints = np.frombuffer(pcm, dtype="<i2")
    # 超范围必须被裁剪，不能溢出
    assert ints.min() >= -32767
    assert ints.max() <= 32767


def test_write_wav_roundtrip(tmp_path: Path) -> None:
    samples = np.sin(np.linspace(0, 20, 24000)).astype(np.float32) * 0.5
    out = tmp_path / "o.wav"
    write_wav(out, samples)
    info = probe(out)
    assert info.sample_rate == TARGET_SAMPLE_RATE
    assert info.channels == 1
    assert info.duration_s == pytest.approx(1.0, abs=0.01)


def test_normalize_peak() -> None:
    samples = np.array([0.1, -0.2, 0.15], dtype=np.float32)
    out = normalize_peak(samples, target_peak=0.95)
    assert float(np.max(np.abs(out))) == pytest.approx(0.95, abs=1e-5)


def test_normalize_silence_safe() -> None:
    """全零输入不能除零。"""
    out = normalize_peak(np.zeros(100, dtype=np.float32))
    assert np.all(out == 0)


# ----------------------------------------------------------------------
# 质量检测
# ----------------------------------------------------------------------


def test_good_audio_passes(tmp_path: Path) -> None:
    p = _make_wav(tmp_path / "good.wav", duration=8.0, amp=0.5)
    report = quality.analyze(p)
    assert report.ok is True
    assert report.score > 0.5
    assert report.blocking_issues == []


def test_too_short_blocked(tmp_path: Path) -> None:
    p = _make_wav(tmp_path / "short.wav", duration=1.0)
    report = quality.analyze(p)
    assert report.ok is False
    assert any(i.code == "too_short" for i in report.blocking_issues)


def test_too_long_warns(tmp_path: Path) -> None:
    p = _make_wav(tmp_path / "long.wav", duration=30.0)
    report = quality.analyze(p)
    assert any(i.code == "too_long" for i in report.warnings)


def test_clipped_blocked(tmp_path: Path) -> None:
    """严重削波必须阻断。"""
    sr = 24000
    t = np.arange(int(8.0 * sr)) / sr
    # 大幅方波，几乎全是削波样本
    data = np.sign(np.sin(2 * np.pi * 200 * t)).astype(np.float32) * 1.0
    p = tmp_path / "clip.wav"
    sf.write(str(p), data, sr, subtype="PCM_16")
    report = quality.analyze(p)
    assert report.ok is False
    assert any(i.code == "clipped" for i in report.blocking_issues)


def test_silence_blocked(tmp_path: Path) -> None:
    """大量静音必须阻断。"""
    sr = 24000
    # 只有 0.5 秒有声，其余 9.5 秒静音
    data = np.zeros(int(10.0 * sr), dtype=np.float32)
    n = int(0.5 * sr)
    data[:n] = np.sin(2 * np.pi * 220 * np.arange(n) / sr) * 0.5
    p = tmp_path / "silent.wav"
    sf.write(str(p), data, sr, subtype="PCM_16")
    report = quality.analyze(p)
    assert report.ok is False
    assert any(
        i.code in ("too_much_silence", "low_snr", "too_quiet")
        for i in report.blocking_issues
    )


def test_too_quiet_blocked(tmp_path: Path) -> None:
    p = _make_wav(tmp_path / "quiet.wav", duration=8.0, amp=0.002)
    report = quality.analyze(p)
    assert report.ok is False
    assert any(i.code == "too_quiet" for i in report.blocking_issues)


def test_report_to_dict_serializable(tmp_path: Path) -> None:
    import json

    p = _make_wav(tmp_path / "d.wav")
    d = quality.analyze(p).to_dict()
    json.dumps(d)  # 不抛异常即可
    assert "issues" in d
    assert isinstance(d["issues"], list)


def test_score_range(tmp_path: Path) -> None:
    for dur in (1.0, 5.0, 8.0, 20.0):
        p = _make_wav(tmp_path / f"s{dur}.wav", duration=dur)
        report = quality.analyze(p)
        assert 0.0 <= report.score <= 1.0


def test_snr_higher_scores_better(tmp_path: Path) -> None:
    """干净音频应比带噪音频得分高。"""
    sr = 24000
    t = np.arange(int(8.0 * sr)) / sr
    clean = np.sin(2 * np.pi * 220 * t) * 0.5
    noisy = clean + np.random.default_rng(0).normal(0, 0.05, clean.shape)

    p1 = tmp_path / "clean.wav"
    p2 = tmp_path / "noisy.wav"
    sf.write(str(p1), clean.astype(np.float32), sr, subtype="PCM_16")
    sf.write(str(p2), noisy.astype(np.float32), sr, subtype="PCM_16")

    r1 = quality.analyze(p1)
    r2 = quality.analyze(p2)
    assert r1.score >= r2.score
