"""TTS 客户端测试：用 MockTransport 模拟服务，不依赖真实 WSL 服务。

覆盖：
- 正常合成与元数据解析
- 服务不可达（必须报错，不得静默降级）
- 超时
- 重复 turn 拒绝
- 空文本拒绝
- 未知 voice_id
"""

from __future__ import annotations

import asyncio
import io
import math
import wave
from pathlib import Path

import httpx
import pytest

from server.tts import (
    TTSBadRequestError,
    TTSClient,
    TTSConfig,
    TTSDuplicateError,
    TTSError,
    TTSTimeoutError,
    TTSUnavailableError,
)


def make_wav_bytes(seconds: float = 1.0, sr: int = 24000) -> bytes:
    """生成测试用 WAV（纯标准库，不依赖 numpy）。"""
    n = int(sr * seconds)
    frames = bytearray()
    for i in range(n):
        v = int(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / sr))
        frames += int(v).to_bytes(2, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return buf.getvalue()


@pytest.fixture()
def cfg(tmp_path: Path) -> TTSConfig:
    return TTSConfig(
        base_url="http://127.0.0.1:50000",
        timeout_s=5.0,
        output_dir=tmp_path,
        default_voice="xiaoman",
    )


def test_synthesize_success(cfg: TTSConfig) -> None:
    wav = make_wav_bytes(2.0)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/synthesize"
        return httpx.Response(
            200, content=wav,
            headers={
                "content-type": "audio/wav",
                "x-sample-rate": "24000",
                "x-duration": "2.000",
                "x-engine": "cosyvoice3",
            },
        )

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            return await tts.synthesize("你好", turn_id="t1")

    result = asyncio.run(go())
    assert result.sample_rate == 24000
    assert result.duration_s == pytest.approx(2.0, abs=0.01)
    assert result.engine == "cosyvoice3"
    assert result.audio_path.exists()
    assert result.audio_path.stat().st_size == len(wav)
    assert result.realtime_factor > 0


def test_output_filename_contains_voice(cfg: TTSConfig) -> None:
    """输出文件名必须带音色标识，否则事后无法区分是哪套音色生成的。"""
    wav = make_wav_bytes(1.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=wav,
                              headers={"x-sample-rate": "24000", "x-duration": "1.0"})

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            return await tts.synthesize("你好", turn_id="t9", voice_id="elysia")

    result = asyncio.run(go())
    assert result.audio_path.name == "tts_elysia_t9.wav"
    assert "elysia" in result.audio_path.name


def test_output_filename_sanitizes_unsafe_voice(cfg: TTSConfig) -> None:
    """音色名含路径分隔符时必须清洗，不能写到目录外。"""
    wav = make_wav_bytes(1.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=wav,
                              headers={"x-sample-rate": "24000", "x-duration": "1.0"})

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            return await tts.synthesize("你好", turn_id="t10", voice_id="../evil")

    result = asyncio.run(go())
    # 必须仍落在 output_dir 内
    assert result.audio_path.parent == cfg.output_dir
    assert ".." not in result.audio_path.name
    assert result.audio_path.name == "tts_evil_t10.wav"


def test_output_name_override_still_wins(cfg: TTSConfig) -> None:
    """显式 output_name 优先于自动命名（保持向后兼容）。"""
    wav = make_wav_bytes(1.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=wav,
                              headers={"x-sample-rate": "24000", "x-duration": "1.0"})

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            return await tts.synthesize("你好", turn_id="t11",
                                        voice_id="elysia", output_name="custom.wav")

    result = asyncio.run(go())
    assert result.audio_path.name == "custom.wav"


def test_headers_missing_falls_back_to_wav_probe(cfg: TTSConfig) -> None:
    """服务没返回元数据头时，应从 WAV 头读取。"""
    wav = make_wav_bytes(1.5)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=wav)

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            return await tts.synthesize("你好", turn_id="t2")

    result = asyncio.run(go())
    assert result.sample_rate == 24000
    assert result.duration_s == pytest.approx(1.5, abs=0.01)


def test_service_unavailable_raises(cfg: TTSConfig) -> None:
    """服务不可达必须明确报错，不得静默降级到云端。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            await tts.synthesize("你好", turn_id="t3")

    with pytest.raises(TTSUnavailableError) as exc:
        asyncio.run(go())
    assert "无法连接" in str(exc.value)


def test_timeout_raises(cfg: TTSConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout")

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            await tts.synthesize("你好", turn_id="t4")

    with pytest.raises(TTSTimeoutError):
        asyncio.run(go())


def test_empty_text_rejected(cfg: TTSConfig) -> None:
    async def go():
        async with TTSClient(cfg) as tts:
            await tts.synthesize("   ", turn_id="t5")

    with pytest.raises(TTSBadRequestError):
        asyncio.run(go())


def test_duplicate_turn_rejected(cfg: TTSConfig) -> None:
    wav = make_wav_bytes(1.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=wav,
                              headers={"x-sample-rate": "24000", "x-duration": "1.0"})

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            await tts.synthesize("一", turn_id="dup")
            await tts.synthesize("二", turn_id="dup")

    with pytest.raises(TTSDuplicateError):
        asyncio.run(go())


def test_reset_turn_allows_retry(cfg: TTSConfig) -> None:
    wav = make_wav_bytes(1.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=wav,
                              headers={"x-sample-rate": "24000", "x-duration": "1.0"})

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            await tts.synthesize("一", turn_id="r1")
            tts.reset_turn("r1")
            return await tts.synthesize("一", turn_id="r1")

    result = asyncio.run(go())
    assert result.duration_s == 1.0


def test_bad_request_400(cfg: TTSConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "unknown voice_id"})

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            await tts.synthesize("你好", voice_id="nobody", turn_id="t6")

    with pytest.raises(TTSBadRequestError):
        asyncio.run(go())


def test_server_error_500(cfg: TTSConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            await tts.synthesize("你好", turn_id="t7")

    with pytest.raises(TTSError):
        asyncio.run(go())


def test_health_ok(cfg: TTSConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "engine": "cosyvoice3"})

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            return await tts.health()

    ok, info = asyncio.run(go())
    assert ok is True
    assert info["engine"] == "cosyvoice3"


def test_health_unavailable(cfg: TTSConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async def go():
        async with TTSClient(cfg, transport=httpx.MockTransport(handler)) as tts:
            return await tts.health()

    ok, info = asyncio.run(go())
    assert ok is False
    assert "error" in info


def test_config_redacted(cfg: TTSConfig) -> None:
    d = cfg.redacted()
    assert d["base_url"] == "http://127.0.0.1:50000"
    assert "timeout_s" in d
