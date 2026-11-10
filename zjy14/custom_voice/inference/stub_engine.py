"""Stub 引擎：不加载真模型，产出可预测的正弦波。

存在意义（Phase 1）：
    让 HTTP 链路、契约测试、日志、数据库全部可验证，而不必等 10s+ 的模型加载。
    Phase 2 换成 CosyVoiceEngine 时，路由代码一行不改。

产出规格（与 ESP32 固件播放格式一致）：
    24kHz / 单声道 / 16bit PCM

时长规则：每个字符 0.2 秒，便于测试断言（"你好" -> 0.4s）。
"""

from __future__ import annotations

import io
import math
import time
import wave
from typing import Any, Iterator

import numpy as np

from ..config.schema import GPUConfig
from ..core.errors import EngineNotReadyError, InvalidRequestError
from ..core.logging import get_logger
from ..schemas.synthesis import (
    AudioChunk,
    EngineStats,
    SynthesisRequest,
    SynthesisResult,
)

logger = get_logger("engine.stub")

#: 固定采样率，与固件播放侧一致
SAMPLE_RATE = 24000

#: 每字符对应的音频时长（秒）
SECONDS_PER_CHAR = 0.2

#: 默认音色
DEFAULT_VOICES = ("elysia",)

#: 正弦波基频（Hz），用中频便于人耳确认"有声音"
TONE_HZ = 440.0


def _make_pcm(text: str, sample_rate: int = SAMPLE_RATE) -> bytes:
    """按字符数生成正弦波 PCM S16LE。"""
    duration = max(len(text), 1) * SECONDS_PER_CHAR
    n = int(duration * sample_rate)
    t = np.arange(n, dtype=np.float64) / sample_rate
    # 加一点淡入淡出，避免首尾爆音
    wave_data = np.sin(2.0 * math.pi * TONE_HZ * t) * 0.25
    fade = int(sample_rate * 0.01)
    if n > 2 * fade > 0:
        ramp = np.linspace(0.0, 1.0, fade)
        wave_data[:fade] *= ramp
        wave_data[-fade:] *= ramp[::-1]
    ints = np.round(np.clip(wave_data, -1.0, 1.0) * 32767.0).astype("<i2")
    return ints.tobytes()


def _to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class StubEngine:
    """占位引擎。实现 ``TTSEngine`` 协议。"""

    kind = "stub"

    def __init__(self, config: GPUConfig) -> None:
        self._config = config
        self._ready = False
        self._load_s = 0.0
        self._warmup_s = 0.0
        self._voices: tuple[str, ...] = DEFAULT_VOICES

    # ------------------------------------------------------------------
    # TTSEngine 协议
    # ------------------------------------------------------------------

    def load(self) -> None:
        """模拟加载。幂等。"""
        if self._ready:
            return
        t0 = time.perf_counter()
        # 模拟一点加载耗时，让 /health 的 load_s 不是恒为 0
        time.sleep(0.05)
        self._load_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        _make_pcm("预热")
        self._warmup_s = time.perf_counter() - t0

        self._ready = True
        logger.info(
            "stub 引擎就绪",
            extra={
                "extra_fields": {
                    "load_s": round(self._load_s, 3),
                    "warmup_s": round(self._warmup_s, 3),
                    "precision": self._config.precision.value,
                    "device": self._config.device,
                }
            },
        )

    def is_ready(self) -> bool:
        return self._ready

    def list_voices(self) -> list[str]:
        return list(self._voices)

    def synthesize(self, req: SynthesisRequest) -> SynthesisResult:
        if not self._ready:
            raise EngineNotReadyError("引擎尚未加载完成，请稍后重试")
        if req.voice_id not in self._voices:
            raise InvalidRequestError(
                f"未知音色 {req.voice_id!r}，可用: {sorted(self._voices)}"
            )

        started = time.perf_counter()
        pcm = _make_pcm(req.text)
        duration = len(pcm) / 2 / SAMPLE_RATE
        total_ms = (time.perf_counter() - started) * 1000
        wav = _to_wav(pcm)

        # 模拟 Phase 2 会有的显存读数；stub 下为 0
        peak_vram_mb = 0.0

        logger.info(
            "stub 合成完成",
            extra={
                "extra_fields": {
                    "voice_id": req.voice_id,
                    "chars": len(req.text),
                    "duration_s": round(duration, 3),
                    "total_ms": round(total_ms, 1),
                    "speed": req.speed,
                }
            },
        )

        return SynthesisResult(
            request_id="",
            sample_rate=SAMPLE_RATE,
            channels=1,
            duration_s=duration,
            total_ms=total_ms,
            first_audio_ms=total_ms,
            rtf=(total_ms / 1000.0) / duration if duration > 0 else 0.0,
            engine=self.kind,
            voice_id=req.voice_id,
            wav_bytes=wav,
            peak_vram_mb=peak_vram_mb,
        )

    def synthesize_stream(self, req: SynthesisRequest) -> Iterator[AudioChunk]:
        """Phase 1：一次性返回整段。Phase 4 换成真流式。"""
        result = self.synthesize(req)
        pcm = result.wav_bytes[44:]  # 去掉 WAV 头，取裸 PCM
        yield AudioChunk(
            sequence=0,
            is_final=True,
            pcm=pcm,
            sample_rate=result.sample_rate,
            channels=result.channels,
        )

    def stats(self) -> EngineStats:
        return EngineStats(
            kind=self.kind,
            ready=self._ready,
            sample_rate=SAMPLE_RATE,
            device=self._config.device,
            gpu_name="(stub)",
            torch_version="",
            load_s=self._load_s,
            warmup_s=self._warmup_s,
            voices=self.list_voices(),
            peak_vram_mb=0.0,
        )

    def diagnostics(self) -> dict[str, Any]:
        """与 CosyVoiceEngine 保持接口一致，便于 /health 统一处理。"""
        return {
            "model_dir": "(stub)",
            "text_frontend": "",
            "voice_count": len(self._voices),
            "hop_len_resets": 0,
            "ratio_warnings": 0,
            "precision": self._config.precision.value,
            "peak_vram_mb": 0.0,
        }

    def shutdown(self) -> None:
        self._ready = False
        logger.info("stub 引擎已关闭")


__all__ = ["DEFAULT_VOICES", "SAMPLE_RATE", "StubEngine"]
