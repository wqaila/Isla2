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

import asyncio
import json
import logging
import struct
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("tts.client")

#: 记住多少个「已完成 turn」用于去重。
#:
#: 去重的目的只是挡住紧随其后的重复调用（看门狗重试、误触发的重入），
#: 保留最近若干条足够。设为无界会让长会话下内存只增不减。
COMPLETED_TURNS_KEEP = 64

#: 单条流式音频块的字节上限。
#:
#: 协议规定单块 ≤ 48KB（见 protocol/messages.py 的 MAX_AUDIO_CHUNK_BYTES），
#: 这里留一倍余量。作用：若对端发来损坏/异常的长度字段，客户端**立刻报错**，
#: 而不是把整个缓冲区无限撑大、一直等到 120s 读超时才失败。
MAX_STREAM_CHUNK_BYTES = 96 * 1024


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
    #: 流式合成接口（边合成边返回 PCM 分块）。服务端不支持时自动回退非流式。
    stream_path: str = "/synthesize_stream"
    health_path: str = "/health"
    timeout_s: float = 120.0
    connect_timeout_s: float = 5.0
    #: 流式读取的「块间空闲超时」（秒）。
    #:
    #: 2026-11-23 新增，修复「板子卡在正在回应、无音频」：
    #:   实测卡死现场：TTS 已下发 386384 字节 / 97 帧（≈8.05s 音频）后
    #:   不再有任何数据，但服务端状态停在 PLAYING 达 134 秒。
    #:
    #:   根因：`_iter_length_prefixed` 收到长度前缀后，若负载没发完
    #:   （`len(buf) < 4 + length`）就 break 回去等下一块 —— 此时
    #:   既不报错也不超时，只能等 httpx 的 read 超时（120s）。
    #:   而看门狗 PLAYBACK_MAX_GUESS_S 也是 120s，两者几乎同时到，
    #:   用户要干等两分钟才看到收尾。
    #:
    #:   改为：只要**连续这么久没有收到任何新字节**，就判定流已死并报错，
    #:   让上层立刻走 TTS 失败收尾，而不是干等总超时。
    #:   取值 20s：正常块间隔最大实测 1.8s，留 10 倍余量。
    stream_idle_timeout_s: float = 20.0
    output_dir: Path = Path("logs/tts")
    default_voice: str = "elysia"
    #: 停顿档位（仅 zjy14 引擎支持，老引擎会忽略这个字段）。
    #:
    #: 2026-11-19 新增。为什么必须**显式**传：
    #:   zjy14 的 `pause_profile` 默认值是 `natural`（180ms），
    #:   它会把回复按标点切成多段、**每段独立调用模型合成**，再插入静音。
    #:   而 zjy13 原来的引擎是整段一次合成。
    #:   也就是说：不传这个字段，升级到 zjy14 后韵律和音色连续性会
    #:   悄悄发生变化，且短段还容易触发音色漂移。
    #:
    #: 这里默认 `none`（整段合成），与 zjy13 原有听感一致；
    #: 想要更自然的句间停顿，把 TTS_PAUSE_PROFILE 设成 natural/relaxed 即可。
    pause_profile: str = "none"

    @classmethod
    def from_env(cls) -> "TTSConfig":
        import os

        def env(name: str, default: str) -> str:
            return os.environ.get(name, default).strip() or default

        return cls(
            base_url=env("TTS_BASE_URL", "http://127.0.0.1:50000").rstrip("/"),
            synthesize_path=env("TTS_SYNTH_PATH", "/synthesize"),
            stream_path=env("TTS_STREAM_PATH", "/synthesize_stream"),
            health_path=env("TTS_HEALTH_PATH", "/health"),
            timeout_s=float(env("TTS_TIMEOUT_S", "120")),
            connect_timeout_s=float(env("TTS_CONNECT_TIMEOUT_S", "5")),
            stream_idle_timeout_s=float(env("TTS_STREAM_IDLE_TIMEOUT_S", "20")),
            output_dir=Path(env("TTS_OUTPUT_DIR", "logs/tts")),
            default_voice=env("TTS_VOICE_ID", "elysia"),
            # 显式传 none（整段合成），保持与 zjy13 原引擎一致的听感。
            # 想要句间停顿可设 TTS_PAUSE_PROFILE=natural / relaxed。
            pause_profile=env("TTS_PAUSE_PROFILE", "none"),
        )

    @property
    def url(self) -> str:
        return f"{self.base_url}{self.synthesize_path}"

    @property
    def stream_url(self) -> str:
        return f"{self.base_url}{self.stream_path}"

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
        #: 已完成的 turn（用于拒绝重复合成）。
        #:
        #: 2026-11-19：改成**有界**的 OrderedDict。原来是无界 set，
        #: 长会话下只增不减（`reset_turn` 无人调用），内存持续增长。
        #: 去重的目的只是「挡住紧随其后的重复调用」，保留最近若干条足够，
        #: 不必记住整个进程生命周期的所有 turn。
        self._completed_turns: OrderedDict[str, None] = OrderedDict()
        #: 最近一次流式合成的采样率（从响应头 x-sample-rate 读取）。
        #: 流式接口是「裸 PCM」，调用方必须知道采样率才能正确下发，
        #: 而首块到达时就要发 playback_start，所以这里缓存最近一次的值。
        self.last_stream_sample_rate: int = 0

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
            # 显式指定停顿档位，避免依赖服务端默认值。
            # zjy14 默认是 natural（180ms 分段），与 zjy13 原引擎的
            # 整段合成听感不同；显式传 none 保持行为一致。
            # 老引擎不认识这个字段会忽略，不会报错。
            "pause_profile": self.config.pause_profile,
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

        if response.status_code in (400, 422):
            # 422 = FastAPI/pydantic 的参数校验失败（与 400 同义：不可重试）
            raise TTSBadRequestError(
                f"TTS 参数错误（{response.status_code}）: {response.text[:300]}",
                status_code=response.status_code,
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
        # 文件名带上音色标识，便于事后区分是哪套音色生成的。
        # 历史文件不改名（避免打乱按时间排序、影响已引用它们的日志与测试）。
        used_voice = _sanitize_voice_id(payload["voice_id"])
        name = output_name or f"tts_{used_voice}_{turn_key}.wav"
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
        self._mark_completed(turn_key)

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
        self._completed_turns.pop(turn_id, None)

    def _mark_completed(self, turn_key: str) -> None:
        """把 turn 标记为已完成，并维持最近 N 条的有界窗口。

        为什么有界（2026-11-19）：去重的目的只是挡住「紧随其后的重复调用」，
        不需要记住整个进程生命周期的所有 turn。原实现是无界 set，
        长会话下只增不减（`reset_turn` 从未被调用），内存缓慢增长。
        """
        self._completed_turns[turn_key] = None
        self._completed_turns.move_to_end(turn_key)
        while len(self._completed_turns) > COMPLETED_TURNS_KEEP:
            self._completed_turns.popitem(last=False)

    # ------------------------------------------------------------------
    # 流式合成
    # ------------------------------------------------------------------

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        """流式合成，逐块产出裸 PCM16LE 单声道数据。

        与非流式 ``synthesize()`` 的区别（2026-11-12 新增）：
        ``synthesize()`` 要等整段音频合成完（实测 8 秒音频需 4.8 秒），
        这期间板子完全静默，是端到端延迟的最大来源。
        本方法在服务端按句流式生成时，**首块通常 1~1.5 秒**就能拿到，
        调用方可以立刻开始下发播放。

        协议：每块 = 4 字节大端 uint32 长度 + PCM；长度 0 表示结束。
        采样率从响应头 ``x-sample-rate`` 读取（默认 24000）。

        :raises TTSUnavailableError: 服务未启动或不支持流式接口
        :raises TTSError: 合成过程中出错，或流被截断
        """
        text = (text or "").strip()
        if not text:
            raise TTSBadRequestError("待合成文本为空")

        client = self._ensure()
        payload = {
            "text": text,
            "voice_id": voice_id or self.config.default_voice,
            "speed": speed,
            # 显式指定停顿档位，避免依赖服务端默认值。
            # zjy14 默认是 natural（180ms 分段），与 zjy13 原引擎的
            # 整段合成听感不同；显式传 none 保持行为一致。
            # 老引擎不认识这个字段会忽略，不会报错。
            "pause_profile": self.config.pause_profile,
        }

        try:
            async with client.stream(
                "POST", self.config.stream_url, json=payload
            ) as response:
                if response.status_code in (400, 422):
                    # 422 也要当成「参数错误」，不能落进泛 TTSError。
                    #
                    # 2026-11-19 修复：zjy14 用 FastAPI + pydantic 做参数校验，
                    # 校验失败返回的是 **422**（不是 400）。原来只认 400，
                    # 于是「文本过长 / 语速越界 / 音色名非法」这类**不可重试**
                    # 的错误被归成 TTSError，而 ws/server.py 把它当作
                    # retryable 的 TTS_FAILED —— 会白白重试，还掩盖了真实原因。
                    body = await response.aread()
                    raise TTSBadRequestError(
                        f"TTS 参数错误（{response.status_code}）: {body[:300]!r}",
                        status_code=response.status_code,
                    )
                if response.status_code == 503:
                    # 503 有多个来源，不能一律说成「模型未加载」：
                    #   - engine_not_ready：模型还在加载
                    #   - queue_timeout  ：推理队列满，等待超时（2026-11-19 新增）
                    # 读响应体把 code/message 带出来，否则排查时会被误导
                    # （曾把「排队超时」误读成「模型没加载」，白查一轮）。
                    body = await response.aread()
                    detail = ""
                    try:
                        err = json.loads(body.decode("utf-8", "replace"))
                        code = err.get("code") or ""
                        msg = err.get("message") or ""
                        detail = f"{code}: {msg}" if code or msg else ""
                    except (ValueError, AttributeError):
                        detail = body[:200].decode("utf-8", "replace")
                    raise TTSUnavailableError(
                        f"TTS 服务暂时不可用（503）{('：' + detail) if detail else ''}"
                    )
                if response.status_code == 404:
                    # 老版本服务端没有流式接口：明确告知，由调用方决定回退
                    raise TTSUnavailableError(
                        "TTS 服务不支持流式接口（/synthesize_stream 返回 404）。"
                        "请更新 WSL 侧的 scripts/tts_server_wsl.py。"
                    )
                if response.status_code != 200:
                    body = await response.aread()
                    raise TTSError(
                        f"TTS 返回 {response.status_code}: {body[:200]!r}",
                        status_code=response.status_code,
                    )

                # 采样率必须在读第一块之前拿到：调用方收到首块就要发
                # playback_start，里面得带采样率。
                try:
                    self.last_stream_sample_rate = int(
                        response.headers.get("x-sample-rate", "0") or 0
                    )
                except (TypeError, ValueError):
                    self.last_stream_sample_rate = 0
                if not self.last_stream_sample_rate:
                    self.last_stream_sample_rate = 24000

                finished = False
                async for pcm in _iter_length_prefixed(
                    _aiter_with_idle_timeout(
                        response.aiter_bytes(),
                        idle_s=self.config.stream_idle_timeout_s,
                    )
                ):
                    if pcm is None:
                        finished = True
                        break
                    yield pcm

                if not finished:
                    raise TTSError("TTS 流被截断（未收到结束标记）")
        except httpx.ConnectError as exc:
            raise TTSUnavailableError(
                f"无法连接本地 TTS 服务 {self.config.base_url}。"
                "请确认 WSL 中的 TTS 服务已启动。"
                f"（{type(exc).__name__}）"
            ) from exc
        except httpx.TimeoutException as exc:
            raise TTSTimeoutError(
                f"TTS 流式合成超时（{self.config.timeout_s}s）"
            ) from exc
        except httpx.HTTPError as exc:
            raise TTSUnavailableError(
                f"TTS 流式请求失败: {type(exc).__name__}: {exc}"
            ) from exc


async def _aiter_with_idle_timeout(
    byte_iter: AsyncIterator[bytes],
    *,
    idle_s: float,
) -> AsyncIterator[bytes]:
    """给异步字节流加「块间空闲超时」。

    2026-11-23 新增，修复「板子卡在正在回应、无音频」的干等问题。

    背景：``_iter_length_prefixed`` 收到长度前缀后若负载未发完，
    会 break 回去等下一块。若对端（TTS 引擎）此时挂死，
    这个等待没有任何上限，只能靠 httpx 的 read 超时（默认 120s）。
    实测用户干等了 134 秒才恢复。

    这里在**每一块**上套 ``asyncio.wait_for``：只要连续 ``idle_s``
    秒没收到任何新字节，就抛 ``TTSTimeoutError``，
    让上层立即走 TTS 失败收尾，而不是等总超时。

    :param byte_iter: 上游字节流（httpx 的 aiter_bytes）
    :param idle_s: 允许的最长无数据间隔（秒）；<=0 表示不限制
    """
    if idle_s <= 0:
        async for chunk in byte_iter:
            yield chunk
        return

    iterator = byte_iter.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=idle_s)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError as exc:
            raise TTSTimeoutError(
                f"TTS 流式合成中断：连续 {idle_s:.0f}s 未收到任何数据，"
                "判定对端已挂死（音频流未正常结束）"
            ) from exc
        yield chunk


async def _iter_length_prefixed(
    byte_iter: AsyncIterator[bytes],
    *,
    max_chunk_bytes: int = MAX_STREAM_CHUNK_BYTES,
) -> AsyncIterator[bytes | None]:
    """解析「4 字节大端长度 + 负载」的分块流。

    长度 0 表示结束，此时产出一次 ``None`` 供调用方确认「流是完整的」。
    这样能区分「正常结束」与「连接中途断掉」——后者不产出 None，
    调用方据此报错，避免把截断的音频当成完整回复播出去。

    ``max_chunk_bytes``（2026-11-19 新增）：
      长度字段来自对端，不能无条件信任。原来没有上限校验，
      一旦读到损坏的长度（比如 0xFFFFFFFF），`buf` 会一直等下去：
      内存无界增长，直到 120s 读超时才失败 —— 期间毫无提示。
      协议规定单块 ≤ 48KB，这里超限直接抛错，让故障立刻可见。
    """
    buf = bytearray()
    async for chunk in byte_iter:
        buf.extend(chunk)
        while True:
            if len(buf) < 4:
                break
            (length,) = struct.unpack(">I", bytes(buf[:4]))
            if length == 0:
                del buf[:4]
                yield None
                return
            if length > max_chunk_bytes:
                raise TTSError(
                    f"TTS 流式分块长度异常：声明 {length} 字节，"
                    f"超过上限 {max_chunk_bytes}（协议规定单块 ≤ 48KB）。"
                    "对端可能发送了损坏的数据。"
                )
            if len(buf) < 4 + length:
                break
            payload = bytes(buf[4:4 + length])
            del buf[:4 + length]
            yield payload


def _sanitize_voice_id(voice_id: str) -> str:
    """把音色名清洗成可安全放进文件名的片段。

    voice_id 来自角色卡配置或调用方，可能含空格、路径分隔符等。
    这里只保留字母数字与下划线连字符，其余替换为下划线；
    全空时回退为 "unknown"，避免出现 `tts_.wav` 这类无名文件。
    """
    import re

    cleaned = re.sub(r"[^0-9A-Za-z_\-]", "_", (voice_id or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "unknown"


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
