"""本地 TTS 客户端。

设计原则（对应 V2 第六节）：
- 封装统一接口 ``synthesize(text, voice_id, speed)``，业务代码不感知具体引擎
- 本地 TTS 走 HTTP，服务只监听回环地址
- 加载失败必须明确报错，**不得静默回退到云端**（V2 第 123 行硬性要求）
- 记录首段音频延迟、完整合成耗时、实时率
- 支持超时、取消、防重复合成

当前实现：CosyVoice3 跑在 WSL2，通过 HTTP 暴露；Windows 侧用本模块调用。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("tts.client")


class TTSError(RuntimeError):
    """TTS 统一异常基类。"""

    code = "tts_error"

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.status_code is not None:
            d["status_code"] = self.status_code
        return d


class TTSUnavailableError(TTSError):
    """本地 TTS 服务未启动或不可达。

    注意：这个错误**不允许**被上层静默降级为云端 TTS。
    V2 第六节第 7 条：除非用户明确配置备用云端 TTS，否则不得偷偷回退。
    """

    code = "tts_unavailable"


class TTSTimeoutError(TTSError):
    code = "tts_timeout"


class TTSBadRequestError(TTSError):
    code = "tts_bad_request"


class TTSDuplicateError(TTSError):
    """同一 turn 重复合成，防止重复计算。"""

    code = "tts_duplicate"


@dataclass(slots=True)
class TTSResult:
    """一次合成的结果与计时。"""

    audio_path: Path
    sample_rate: int
    duration_s: float
    first_audio_ms: float | None
    total_ms: float
    realtime_factor: float
    text: str
    voice_id: str = ""
    engine: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "audio_path": str(self.audio_path),
            "sample_rate": self.sample_rate,
            "duration_s": round(self.duration_s, 3),
            "first_audio_ms": round(self.first_audio_ms, 1) if self.first_audio_ms else None,
            "total_ms": round(self.total_ms, 1),
            "realtime_factor": round(self.realtime_factor, 4),
            "voice_id": self.voice_id,
            "engine": self.engine,
            **self.extra,
        }

    def summary(self) -> str:
        return (
            f"{self.duration_s:.2f}s 音频 / {self.total_ms:.0f}ms 合成 "
            f"/ RTF {self.realtime_factor:.3f}"
        )


@dataclass(frozen=True, slots=True)
class TTSConfig:
    """TTS 客户端配置。"""

    base_url: str = "http://127.0.0.1:50000"
    synthesize_path: str = "/synthesize"
    health_path: str = "/health"
    timeout_s: float = 120.0
    connect_timeout_s: float = 5.0
    output_dir: Path = Path("logs/tts")
    default_voice: str = "xiaoman"

    @classmethod
    def from_env(cls) -> "TTSConfig":
        import os

        def env(name: str, default: str) -> str:
            return os.environ.get(name, default).strip() or default

        return cls(
            base_url=env("TTS_BASE_URL", "http://127.0.0.1:50000").rstrip("/"),
            synthesize_path=env("TTS_SYNTH_PATH", "/synthesize"),
            health_path=env("TTS_HEALTH_PATH", "/health"),
            timeout_s=float(env("TTS_TIMEOUT_S", "120")),
            connect_timeout_s=float(env("TTS_CONNECT_TIMEOUT_S", "5")),
            output_dir=Path(env("TTS_OUTPUT_DIR", "logs/tts")),
            default_voice=env("TTS_VOICE_ID", "xiaoman"),
        )

    @property
    def url(self) -> str:
        return f"{self.base_url}{self.synthesize_path}"

    @property
    def health_url(self) -> str:
        return f"{self.base_url}{self.health_path}"

    def redacted(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "timeout_s": self.timeout_s,
            "output_dir": str(self.output_dir),
            "default_voice": self.default_voice,
        }


class TTSClient:
    """本地 TTS 客户端（HTTP）。

    用法::

        async with TTSClient(cfg) as tts:
            ok, info = await tts.health()
            result = await tts.synthesize("你好呀", turn_id="t-1")
    """

    def __init__(
        self,
        config: TTSConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config or TTSConfig.from_env()
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._active_turns: set[str] = set()
        self._completed_turns: set[str] = set()

    # ------------------------------------------------------------------

    async def __aenter__(self) -> "TTSClient":
        self._ensure()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    def _ensure(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            timeout = httpx.Timeout(
                connect=self.config.connect_timeout_s,
                read=self.config.timeout_s,
                write=self.config.timeout_s,
                pool=self.config.connect_timeout_s,
            )
            self._client = httpx.AsyncClient(
                timeout=timeout,
                transport=self._transport,
                trust_env=False,   # 不走系统代理，本地回环直连
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ------------------------------------------------------------------

    async def health(self) -> tuple[bool, dict[str, Any]]:
        """检查 TTS 服务是否可用。返回 (是否可用, 详情)。"""
        client = self._ensure()
        try:
            r = await client.get(self.config.health_url, timeout=5.0)
            if r.status_code == 200:
                return True, r.json()
            return False, {"status_code": r.status_code, "body": r.text[:200]}
        except httpx.HTTPError as exc:
            return False, {"error": f"{type(exc).__name__}: {exc}"}

    async def synthesize(
        self,
        text: str,
        *,
        turn_id: str | None = None,
        voice_id: str | None = None,
        speed: float = 1.0,
        output_name: str | None = None,
    ) -> TTSResult:
        """合成语音。

        :raises TTSUnavailableError: 服务未启动
        :raises TTSTimeoutError: 超时
        :raises TTSBadRequestError: 参数错误
        :raises TTSDuplicateError: 同一 turn 重复合成
        """
        text = (text or "").strip()
        if not text:
            raise TTSBadRequestError("待合成文本为空")

        turn_key = turn_id or f"auto-{uuid.uuid4().hex[:12]}"
        if turn_key in self._active_turns:
            raise TTSDuplicateError(f"turn {turn_key!r} 正在合成，拒绝重复调用")
        if turn_key in self._completed_turns:
            raise TTSDuplicateError(
                f"turn {turn_key!r} 已合成过，拒绝重复调用（避免重复计算）"
            )
        self._active_turns.add(turn_key)

        client = self._ensure()
        payload = {
            "text": text,
            "voice_id": voice_id or self.config.default_voice,
            "speed": speed,
        }

        started = time.perf_counter()
        try:
            response = await client.post(self.config.url, json=payload)
        except httpx.ConnectError as exc:
            raise TTSUnavailableError(
                f"无法连接本地 TTS 服务 {self.config.base_url}。"
                "请确认 WSL 中的 TTS 服务已启动。"
                f"（{type(exc).__name__}）"
            ) from exc
        except httpx.TimeoutException as exc:
            raise TTSTimeoutError(
                f"TTS 合成超时（{self.config.timeout_s}s）"
            ) from exc
        except httpx.HTTPError as exc:
            raise TTSUnavailableError(f"TTS 请求失败: {type(exc).__name__}: {exc}") from exc
        finally:
            self._active_turns.discard(turn_key)

        total_ms = (time.perf_counter() - started) * 1000

        if response.status_code == 400:
            raise TTSBadRequestError(
                f"TTS 参数错误: {response.text[:300]}", status_code=400
            )
        if response.status_code >= 500:
            raise TTSError(
                f"TTS 服务内部错误: {response.text[:300]}",
                status_code=response.status_code,
            )
        if response.status_code != 200:
            raise TTSError(
                f"TTS 返回意外状态码 {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        # 服务返回 wav 二进制 + 头部元数据
        out_dir = self.config.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        name = output_name or f"tts_{turn_key}.wav"
        out_path = out_dir / name
        out_path.write_bytes(response.content)

        sample_rate = int(response.headers.get("x-sample-rate", "0") or 0)
        duration = float(response.headers.get("x-duration", "0") or 0)
        first_ms = response.headers.get("x-first-audio-ms")
        engine = response.headers.get("x-engine", "")

        if not sample_rate or not duration:
            # 头部缺失时从 WAV 头读
            sample_rate, duration = _probe_wav(out_path)

        rtf = (total_ms / 1000) / duration if duration > 0 else 0.0
        self._completed_turns.add(turn_key)

        logger.info(
            "tts ok turn=%s dur=%.2fs total=%.0fms rtf=%.3f",
            turn_key, duration, total_ms, rtf,
        )

        return TTSResult(
            audio_path=out_path,
            sample_rate=sample_rate,
            duration_s=duration,
            first_audio_ms=float(first_ms) if first_ms else None,
            total_ms=total_ms,
            realtime_factor=rtf,
            text=text,
            voice_id=payload["voice_id"],
            engine=engine,
        )

    def reset_turn(self, turn_id: str) -> None:
        """允许显式重试同一 turn。"""
        self._completed_turns.discard(turn_id)


def _probe_wav(path: Path) -> tuple[int, float]:
    """从 WAV 头读取采样率与时长。"""
    import wave

    try:
        with wave.open(str(path), "rb") as w:
            sr = w.getframerate()
            return sr, w.getnframes() / sr if sr else 0.0
    except Exception:  # noqa: BLE001
        return 0, 0.0


__all__ = [
    "TTSClient",
    "TTSConfig",
    "TTSResult",
    "TTSError",
    "TTSUnavailableError",
    "TTSTimeoutError",
    "TTSBadRequestError",
    "TTSDuplicateError",
]
