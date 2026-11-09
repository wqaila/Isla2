"""Stub 引擎测试：验证产出格式与协议行为。"""

from __future__ import annotations

import io
import wave

import pytest

from custom_voice.core.errors import EngineNotReadyError, InvalidRequestError
from custom_voice.inference.engine import TTSEngine
from custom_voice.inference.stub_engine import SAMPLE_RATE, StubEngine
from custom_voice.schemas.synthesis import SynthesisRequest


def _read_wav(data: bytes) -> tuple[int, int, int, float]:
    """返回 (采样率, 声道, 位深, 时长)。"""
    with wave.open(io.BytesIO(data), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        frames = w.getnframes()
    return sr, ch, width * 8, frames / sr


@pytest.fixture()
def engine(gpu_config) -> StubEngine:  # type: ignore[no-untyped-def]
    e = StubEngine(gpu_config)
    e.load()
    return e


def test_satisfies_protocol(engine: StubEngine) -> None:
    """StubEngine 必须满足 TTSEngine 协议，Phase 2 才能无缝替换。"""
    assert isinstance(engine, TTSEngine)


def test_not_ready_raises(gpu_config) -> None:  # type: ignore[no-untyped-def]
    e = StubEngine(gpu_config)
    with pytest.raises(EngineNotReadyError):
        e.synthesize(SynthesisRequest(text="你好"))


def test_load_is_idempotent(engine: StubEngine) -> None:
    first = engine.stats().load_s
    engine.load()
    assert engine.stats().load_s == first


def test_wav_format_matches_firmware(engine: StubEngine) -> None:
    """产出必须是 24kHz / 单声道 / 16bit —— 与 ESP32 固件播放格式一致。"""
    result = engine.synthesize(SynthesisRequest(text="你好"))
    sr, ch, bits, duration = _read_wav(result.wav_bytes)
    assert sr == SAMPLE_RATE == 24000
    assert ch == 1
    assert bits == 16
    assert duration > 0


def test_duration_scales_with_length(engine: StubEngine) -> None:
    """时长 = 字符数 × 0.2s，便于测试断言。"""
    r2 = engine.synthesize(SynthesisRequest(text="你好"))
    r10 = engine.synthesize(SynthesisRequest(text="一二三四五六七八九十"))
    assert r2.duration_s == pytest.approx(0.4, abs=0.01)
    assert r10.duration_s == pytest.approx(2.0, abs=0.01)
    assert r10.duration_s > r2.duration_s


def test_unknown_voice_rejected(engine: StubEngine) -> None:
    with pytest.raises(InvalidRequestError):
        engine.synthesize(SynthesisRequest(text="你好", voice_id="nobody"))


def test_rtf_positive(engine: StubEngine) -> None:
    result = engine.synthesize(SynthesisRequest(text="你好"))
    assert result.rtf > 0


def test_stream_yields_single_final_chunk(engine: StubEngine) -> None:
    chunks = list(engine.synthesize_stream(SynthesisRequest(text="你好")))
    assert len(chunks) == 1
    assert chunks[0].is_final is True
    assert chunks[0].sequence == 0
    assert chunks[0].sample_rate == SAMPLE_RATE


def test_stats_reflect_state(engine: StubEngine) -> None:
    s = engine.stats()
    assert s.ready is True
    assert s.kind == "stub"
    assert s.sample_rate == SAMPLE_RATE
    assert "elysia" in s.voices


def test_shutdown_marks_not_ready(engine: StubEngine) -> None:
    engine.shutdown()
    assert engine.is_ready() is False
