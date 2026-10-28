"""WebSocket 服务端：接收开发板音频，跑 ASR → LLM → TTS，推回结果。

对应 V2 第十一、十二节。

设计要点：
- **控制消息 JSON，音频二进制帧**（V2 第十一节）
- **turn_id 贯穿全程**，过期响应直接丢弃，不覆盖新轮次
- **半双工**：处理期间不接受新录音（V2 第九节）
- **服务只监听 127.0.0.1 或指定内网地址**，需显式配置访问控制
- 处理逻辑复用已有模块：asr / server.llm / server.tts / server.dialogue

运行（需先启动 WSL 的 TTS 服务）：
    python -m server.ws.server --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.server import ServerConnection

from ..config import LLMConfig, load_env_file
from ..dialogue.character import CharacterCard
from ..dialogue.session import ConversationSession
from ..llm import LLMClient, LLMError
from ..protocol import (
    ASR_FINAL,
    BINARY_HEADER_SIZE,
    CANCEL,
    DEVICE_HELLO,
    ERROR,
    HEARTBEAT,
    HEARTBEAT_ACK,
    LLM_DELTA,
    LLM_FINAL,
    LLM_START,
    MAX_JSON_BYTES,
    PLAYBACK_DONE,
    PLAYBACK_START,
    PROTOCOL_VERSION,
    RECORDING_START,
    RECORDING_STOP,
    SERVER_HELLO,
    SESSION_START,
    TTS_START,
    TURN_CANCELLED,
    TURN_DONE,
    AudioFrame,
    BinaryType,
    Envelope,
    ErrorCode,
    ProtocolError,
    make_error,
    make_hello_ack,
    validate_inbound,
    validate_text,
)
from ..tts import TTSClient, TTSConfig, TTSError, TTSUnavailableError
from .audio import AudioFormat, TurnAudio, encode_pcm16_wav
from .states import DialogueStateMachine, IllegalTransitionError, State

logger = logging.getLogger("ws.server")

#: 心跳超时（秒）——超过则断开，V2 第十一节要求连接管理
HEARTBEAT_TIMEOUT_S = 30.0

#: 单轮处理总超时
TURN_TIMEOUT_S = 60.0


@dataclass(slots=True)
class ServerConfig:
    """服务端配置。"""

    host: str = "127.0.0.1"
    port: int = 8765
    character_id: str = "default"
    #: 期望开发板发送的格式（会通过 server_hello 告知）
    audio_in_sample_rate: int = 16000
    audio_in_channels: int = 4
    #: 服务端下发的音频格式
    audio_out_sample_rate: int = 24000
    #: 允许的设备令牌（空表示不校验；V2 第十七节要求基本访问控制）
    access_token: str = ""
    max_connections: int = 4


@dataclass(slots=True)
class ClientSession:
    """一个开发板连接的会话状态。"""

    ws: Any
    session_id: str
    state: DialogueStateMachine = field(default_factory=DialogueStateMachine)
    audio: TurnAudio | None = None
    turn_id: str = ""
    turn_seq: int = 0
    #: 当前轮次的处理任务，用于取消
    task: asyncio.Task | None = None
    last_heartbeat: float = field(default_factory=time.perf_counter)
    device_id: str = ""
    hello_done: bool = False
    #: 本轮应下发的音频字节数与帧数（用于 playback_done 校验）
    expect_audio_bytes: int = 0
    expect_audio_frames: int = 0

    def next_seq(self) -> int:
        self.turn_seq += 1
        return self.turn_seq

    def new_turn(self) -> str:
        self.turn_seq = 0
        self.turn_id = f"{self.session_id}-t{int(time.time()*1000)%100000}"
        return self.turn_id


class DialogueServer:
    """WebSocket 对话服务。"""

    def __init__(
        self,
        config: ServerConfig,
        *,
        llm_client: LLMClient,
        tts_client: TTSClient,
        card: CharacterCard,
        asr_engine: Any = None,
    ) -> None:
        self.config = config
        self.llm = llm_client
        self.tts = tts_client
        self.card = card
        self.asr = asr_engine
        self._clients: dict[str, ClientSession] = {}

    # ------------------------------------------------------------------
    # 发送辅助
    # ------------------------------------------------------------------

    async def send_json(self, client: ClientSession, msg: dict[str, Any]) -> None:
        data = json.dumps(msg, ensure_ascii=False)
        if len(data.encode("utf-8")) > MAX_JSON_BYTES:
            logger.error("出站 JSON 过大，已丢弃: %d 字节", len(data))
            return
        try:
            await client.ws.send(data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("发送失败（连接可能已断）: %s", exc)

    async def send_error(
        self,
        client: ClientSession,
        code: str,
        message: str,
        *,
        fatal: bool = False,
    ) -> None:
        await self.send_json(
            client,
            make_error(
                code, message,
                session_id=client.session_id,
                turn_id=client.turn_id,
                fatal=fatal,
            ),
        )

    async def send_audio(self, client: ClientSession, pcm: bytes, seq: int) -> None:
        """下发 TTS 音频二进制帧。"""
        from ..protocol import turn_id_to_short

        frame = AudioFrame(
            msg_type=BinaryType.AUDIO_DOWN,
            seq=seq,
            turn_id_short=turn_id_to_short(client.turn_id),
            payload=pcm,
        )
        try:
            await client.ws.send(frame.encode())
        except Exception as exc:  # noqa: BLE001
            logger.debug("发送音频失败: %s", exc)

    # ------------------------------------------------------------------
    # 连接生命周期
    # ------------------------------------------------------------------

    async def handler(self, ws: Any) -> None:
        session_id = uuid.uuid4().hex[:12]
        client = ClientSession(ws=ws, session_id=session_id)
        self._clients[session_id] = client
        peer = getattr(ws, "remote_address", None)
        logger.info("连接建立 session=%s peer=%s", session_id, peer)

        try:
            await self._recv_loop(client)
        except websockets.exceptions.ConnectionClosed:
            logger.info("连接关闭 session=%s", session_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("连接异常 session=%s: %s", session_id, exc)
        finally:
            await self._cleanup(client)

    async def _cleanup(self, client: ClientSession) -> None:
        """断开时释放资源（V2 第十二节第 8 条）。"""
        if client.task and not client.task.done():
            client.task.cancel()
            try:
                await client.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        client.audio = None
        client.state.reset()
        self._clients.pop(client.session_id, None)
        logger.info("资源已释放 session=%s", client.session_id)

    async def _recv_loop(self, client: ClientSession) -> None:
        async for raw in client.ws:
            client.last_heartbeat = time.perf_counter()

            if isinstance(raw, (bytes, bytearray)):
                await self._on_binary(client, bytes(raw))
                continue

            await self._on_json(client, raw)

    async def _on_json(self, client: ClientSession, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self.send_error(client, ErrorCode.BAD_MESSAGE, "JSON 解析失败")
            return

        try:
            mtype = validate_inbound(msg)
        except ProtocolError as exc:
            fatal = exc.code == ErrorCode.PROTOCOL_VERSION_MISMATCH
            await self.send_error(client, exc.code, exc.message, fatal=fatal)
            if fatal:
                await client.ws.close()
            return

        # 未握手前只接受 device_hello
        if not client.hello_done and mtype != DEVICE_HELLO:
            await self.send_error(
                client, ErrorCode.NOT_HELLO, "请先发送 device_hello"
            )
            return

        handler = {
            DEVICE_HELLO: self._on_hello,
            RECORDING_START: self._on_recording_start,
            RECORDING_STOP: self._on_recording_stop,
            CANCEL: self._on_cancel,
            PLAYBACK_DONE: self._on_playback_done,
            HEARTBEAT: self._on_heartbeat,
        }.get(mtype)

        if handler is None:
            # 开发板不该发的类型（服务端专用）或不影响状态的消息
            if mtype in (PLAYBACK_START, LLM_DELTA, LLM_FINAL, ASR_FINAL,
                         LLM_START, TTS_START, TURN_DONE, TURN_CANCELLED,
                         SERVER_HELLO, SESSION_START):
                await self.send_error(
                    client, ErrorCode.BAD_MESSAGE,
                    f"消息类型 {mtype} 应由服务端发送",
                )
            return

        await handler(client, msg)

    async def _on_binary(self, client: ClientSession, data: bytes) -> None:
        try:
            frame = AudioFrame.decode(data)
        except ValueError as exc:
            await self.send_error(client, ErrorCode.BAD_MESSAGE, f"音频帧非法: {exc}")
            return

        if frame.msg_type != BinaryType.AUDIO_UP:
            await self.send_error(
                client, ErrorCode.BAD_MESSAGE, "收到非上行音频帧"
            )
            return

        if client.state.state != State.RECORDING:
            # 不在录音状态时丢弃，避免缓冲堆积
            logger.debug("非录音状态，丢弃音频帧 seq=%d", frame.seq)
            return

        if client.audio is None:
            await self.send_error(client, ErrorCode.NO_SUCH_TURN, "没有进行中的录音")
            return

        try:
            client.audio.append(frame.seq, frame.payload)
        except ProtocolError as exc:
            await self.send_error(client, exc.code, exc.message)
            await self._finish_turn(client, reason="audio_overflow")

    # ------------------------------------------------------------------
    # 消息处理
    # ------------------------------------------------------------------

    async def _on_hello(self, client: ClientSession, msg: dict[str, Any]) -> None:
        token = str(msg.get("token", ""))
        if self.config.access_token and token != self.config.access_token:
            await self.send_error(
                client, "unauthorized", "设备令牌无效", fatal=True
            )
            await client.ws.close()
            return

        client.device_id = str(msg.get("device_id", ""))[:64]
        client.hello_done = True
        logger.info(
            "设备握手 device=%s firmware=%s", client.device_id, msg.get("firmware")
        )
        await self.send_json(
            client,
            make_hello_ack(
                server_name="ai-role",
                audio_in_sample_rate=self.config.audio_in_sample_rate,
                audio_in_channels=self.config.audio_in_channels,
                audio_out_sample_rate=self.config.audio_out_sample_rate,
            ),
        )

    async def _on_heartbeat(self, client: ClientSession, msg: dict[str, Any]) -> None:
        await self.send_json(
            client,
            Envelope(type=HEARTBEAT_ACK, session_id=client.session_id).to_dict(),
        )

    async def _on_recording_start(
        self, client: ClientSession, msg: dict[str, Any]
    ) -> None:
        # 半双工：处理中不接受新录音（V2 第九节）
        if client.state.is_busy:
            await self.send_error(
                client, ErrorCode.TURN_BUSY,
                f"当前状态 {client.state.state.value} 不接受新录音",
            )
            return

        try:
            client.state.to(State.RECORDING)
        except IllegalTransitionError as exc:
            await self.send_error(client, ErrorCode.INTERNAL, str(exc))
            return

        turn_id = client.new_turn()
        fmt = AudioFormat(
            sample_rate=int(msg.get("sample_rate", self.config.audio_in_sample_rate)),
            channels=int(msg.get("channels", self.config.audio_in_channels)),
            bits=int(msg.get("bits", 16)),
            encoding=str(msg.get("encoding", "pcm_s16le")),
            channel_layout=str(msg.get("channel_layout", "RMNM")),
        )
        client.audio = TurnAudio(format=fmt)

        logger.info(
            "开始录音 turn=%s fmt=%s", turn_id, fmt.describe()
        )
        await self.send_json(
            client,
            Envelope(
                type=SESSION_START,
                session_id=client.session_id,
                turn_id=turn_id,
                payload={"state": State.RECORDING.value},
            ).to_dict(),
        )

    async def _on_recording_stop(
        self, client: ClientSession, msg: dict[str, Any]
    ) -> None:
        if client.state.state != State.RECORDING:
            await self.send_error(
                client, ErrorCode.BAD_MESSAGE,
                f"当前状态 {client.state.state.value} 无法结束录音",
            )
            return

        audio = client.audio
        if audio is None or audio.chunks == 0:
            await self._finish_turn(client, reason="empty_audio")
            await self.send_error(client, ErrorCode.ASR_EMPTY, "没有收到音频数据")
            return

        logger.info(
            "录音结束 turn=%s 块=%d 字节=%d 乱序=%d 重复=%d",
            client.turn_id, audio.chunks, audio.total_bytes,
            audio.out_of_order, audio.duplicates,
        )
        client.task = asyncio.create_task(self._process_turn(client))

    async def _on_cancel(self, client: ClientSession, msg: dict[str, Any]) -> None:
        turn_id = str(msg.get("turn_id", client.turn_id))
        if client.task and not client.task.done():
            client.task.cancel()
            try:
                await client.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        client.state.reset()
        client.audio = None
        logger.info("轮次已取消 turn=%s", turn_id)
        await self.send_json(
            client,
            Envelope(
                type=TURN_CANCELLED,
                session_id=client.session_id,
                turn_id=turn_id,
                payload={"reason": "client_cancel"},
            ).to_dict(),
        )

    async def _on_playback_done(
        self, client: ClientSession, msg: dict[str, Any]
    ) -> None:
        if client.state.state != State.PLAYING:
            await self.send_error(
                client, ErrorCode.BAD_MESSAGE,
                f"当前状态 {client.state.state.value} 不是播放中",
            )
            return

        # 校验客户端报告的接收量，防止提前退出导致丢帧而不被发现
        reported = msg.get("received_bytes")
        expected = client.expect_audio_bytes
        if reported is not None and expected:
            try:
                got = int(reported)
            except (TypeError, ValueError):
                got = -1
            if got != expected:
                logger.warning(
                    "音频接收不完整 turn=%s 客户端收到 %d / 期望 %d 字节",
                    client.turn_id, got, expected,
                )
                await self.send_error(
                    client, ErrorCode.BAD_MESSAGE,
                    f"音频接收不完整: {got}/{expected} 字节",
                )

        client.expect_audio_bytes = 0
        client.expect_audio_frames = 0
        await self._finish_turn(client, reason="playback_done")

    # ------------------------------------------------------------------
    # 一轮处理
    # ------------------------------------------------------------------

    async def _process_turn(self, client: ClientSession) -> None:
        """ASR → LLM → TTS，全程带 turn_id 校验。"""
        my_turn = client.turn_id
        started = time.perf_counter()
        try:
            async with asyncio.timeout(TURN_TIMEOUT_S):
                await self._run_pipeline(client, my_turn, started)
        except asyncio.TimeoutError:
            logger.warning("轮次超时 turn=%s", my_turn)
            client.state.fail("处理超时")
            await self.send_error(client, ErrorCode.INTERNAL, "本轮处理超时")
            await self._finish_turn(client, reason="timeout")
        except asyncio.CancelledError:
            logger.info("轮次被取消 turn=%s", my_turn)
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("轮次异常 turn=%s", my_turn)
            client.state.fail(str(exc))
            await self.send_error(client, ErrorCode.INTERNAL, f"内部错误: {exc}")
            await self._finish_turn(client, reason="error")

    async def _run_pipeline(
        self, client: ClientSession, my_turn: str, started: float
    ) -> None:
        # ---- ASR ----
        client.state.to(State.ASR_PROCESSING)
        audio = client.audio
        assert audio is not None

        try:
            mono = audio.to_mono_16k()
        except Exception as exc:  # noqa: BLE001
            await self.send_error(client, ErrorCode.ASR_FAILED, f"音频转换失败: {exc}")
            await self._finish_turn(client, reason="audio_convert_failed")
            return

        if mono.size == 0:
            await self.send_error(client, ErrorCode.ASR_EMPTY, "音频为空")
            await self._finish_turn(client, reason="empty")
            return

        duration = mono.size / 16000
        text = ""
        if self.asr is None:
            logger.warning("ASR 引擎未配置，跳过识别")
        else:
            try:
                wav = encode_pcm16_wav(mono, 16000)
                text = await asyncio.to_thread(self._transcribe_bytes, wav)
            except Exception as exc:  # noqa: BLE001
                await self.send_error(
                    client, ErrorCode.ASR_FAILED, f"识别失败: {exc}"
                )
                await self._finish_turn(client, reason="asr_failed")
                return

        if not text.strip():
            await self.send_error(client, ErrorCode.ASR_EMPTY, "未识别到有效语音")
            await self._finish_turn(client, reason="asr_empty")
            return

        logger.info(
            "ASR 完成 turn=%s 时长=%.2fs 文本=%r 耗时=%.0fms",
            my_turn, duration, text, (time.perf_counter() - started) * 1000,
        )
        await self.send_json(
            client,
            Envelope(
                type=ASR_FINAL,
                session_id=client.session_id,
                turn_id=my_turn,
                seq=client.next_seq(),
                payload={"text": text, "duration_s": round(duration, 3)},
            ).to_dict(),
        )

        # ---- LLM ----
        client.state.to(State.LLM_PROCESSING)
        await self.send_json(
            client,
            Envelope(
                type=LLM_START,
                session_id=client.session_id,
                turn_id=my_turn,
                seq=client.next_seq(),
            ).to_dict(),
        )

        session = ConversationSession(self.card, self.llm)
        deltas: list[str] = []

        def on_delta(chunk: str) -> None:
            deltas.append(chunk)

        try:
            record = await session.send(text, on_delta=on_delta)
        except LLMError as exc:
            await self.send_error(
                client, ErrorCode.LLM_FAILED, f"{exc.code}: {exc.message}"
            )
            await self._finish_turn(client, reason="llm_failed")
            return

        reply = record.assistant_text
        logger.info(
            "LLM 完成 turn=%s 首字=%.0fms 总=%.0fms 回复=%r",
            my_turn,
            record.first_token_ms or 0,
            record.total_ms,
            reply[:40],
        )

        # 流式片段逐条下发（客户端可边收边显示）
        for chunk in deltas:
            await self.send_json(
                client,
                Envelope(
                    type=LLM_DELTA,
                    session_id=client.session_id,
                    turn_id=my_turn,
                    seq=client.next_seq(),
                    payload={"text": chunk},
                ).to_dict(),
            )

        await self.send_json(
            client,
            Envelope(
                type=LLM_FINAL,
                session_id=client.session_id,
                turn_id=my_turn,
                seq=client.next_seq(),
                payload={
                    "text": reply,
                    "first_token_ms": record.first_token_ms,
                    "total_ms": record.total_ms,
                },
            ).to_dict(),
        )

        if not reply.strip():
            await self._finish_turn(client, reason="empty_reply")
            return

        # ---- TTS ----
        client.state.to(State.TTS_PROCESSING)
        await self.send_json(
            client,
            Envelope(
                type=TTS_START,
                session_id=client.session_id,
                turn_id=my_turn,
                seq=client.next_seq(),
            ).to_dict(),
        )

        try:
            tts_result = await self.tts.synthesize(
                reply, turn_id=f"{my_turn}-tts"
            )
        except TTSUnavailableError as exc:
            await self.send_error(
                client, ErrorCode.TTS_UNAVAILABLE, exc.message
            )
            await self._finish_turn(client, reason="tts_unavailable")
            return
        except TTSError as exc:
            await self.send_error(client, ErrorCode.TTS_FAILED, exc.message)
            await self._finish_turn(client, reason="tts_failed")
            return

        logger.info(
            "TTS 完成 turn=%s 音频=%.2fs 耗时=%.0fms RTF=%.3f",
            my_turn, tts_result.duration_s, tts_result.total_ms,
            tts_result.realtime_factor,
        )

        # 下发音频（按帧切分，单帧不超过协议上限）
        pcm = _wav_to_pcm16(tts_result.audio_path)
        client.state.to(State.PLAYING)

        step = 4096  # 4096 字节 ≈ 128ms @ 16kHz16bit，兼顾延迟与开销
        total_frames = (len(pcm) + step - 1) // step
        # 记录期望值，供 playback_done 校验（防止客户端提前退出导致丢帧）
        client.expect_audio_bytes = len(pcm)
        client.expect_audio_frames = total_frames

        await self.send_json(
            client,
            Envelope(
                type=PLAYBACK_START,
                session_id=client.session_id,
                turn_id=my_turn,
                seq=client.next_seq(),
                payload={
                    "sample_rate": tts_result.sample_rate,
                    "duration_s": round(tts_result.duration_s, 3),
                    "bytes": len(pcm),
                    "frames": total_frames,
                    "frame_bytes": step,
                },
            ).to_dict(),
        )

        seq = 0
        for off in range(0, len(pcm), step):
            await self.send_audio(client, pcm[off:off + step], seq)
            seq += 1

        total_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "音频已下发 turn=%s 帧=%d/%d 字节=%d 端到端=%.0fms（等待 playback_done）",
            my_turn, seq, total_frames, len(pcm), total_ms,
        )
        # 等待开发板回报 playback_done（由 _on_playback_done 收尾）

    # ------------------------------------------------------------------

    def _transcribe_bytes(self, wav: bytes) -> str:
        """在 ASR 引擎上识别一段 WAV 字节。"""
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp(prefix="ws_asr_")) / "in.wav"
        try:
            tmp.write_bytes(wav)
            result = self.asr.transcribe_file(tmp)
            return result.text
        finally:
            try:
                tmp.unlink(missing_ok=True)
                tmp.parent.rmdir()
            except OSError:
                pass

    async def _finish_turn(self, client: ClientSession, *, reason: str) -> None:
        """结束一轮，回到 IDLE（V2 第十二节第 9 条）。"""
        turn_id = client.turn_id
        client.audio = None
        try:
            client.state.to(State.IDLE)
        except IllegalTransitionError:
            client.state.reset()

        await self.send_json(
            client,
            Envelope(
                type=TURN_DONE,
                session_id=client.session_id,
                turn_id=turn_id,
                seq=client.next_seq(),
                payload={"reason": reason, "state": State.IDLE.value},
            ).to_dict(),
        )

    # ------------------------------------------------------------------

    async def serve(self) -> None:
        logger.info(
            "启动 WebSocket 服务 ws://%s:%d (字符=%s)",
            self.config.host, self.config.port, self.card.name,
        )
        async with websockets.serve(
            self.handler,
            self.config.host,
            self.config.port,
            max_size=MAX_JSON_BYTES,
            ping_interval=20,
            ping_timeout=20,
        ):
            await asyncio.Future()


def _wav_to_pcm16(path: Any) -> bytes:
    """读取 WAV，返回裸 PCM16 数据（剥掉 WAV 头）。

    开发板需要裸 PCM 才能直接送 I2S，不需要 WAV 头。
    """
    import wave

    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"仅支持 16 bit WAV，实际 {w.getsampwidth()*8} bit")
        return w.readframes(w.getnframes())


async def main_async(args: argparse.Namespace) -> int:
    from pathlib import Path

    load_env_file(Path(args.env))

    llm_cfg = LLMConfig.from_env()
    llm_cfg.validate()
    tts_cfg = TTSConfig.from_env()

    card = CharacterCard.from_file(args.character_file)

    cfg = ServerConfig(
        host=args.host,
        port=args.port,
        character_id=card.character_id,
        audio_in_sample_rate=args.in_sample_rate,
        audio_in_channels=args.in_channels,
        access_token=args.token,
    )

    asr_engine = None
    if not args.no_asr:
        try:
            from asr.engine import ASREngine

            asr_engine = ASREngine(use_vad=False)
            logger.info("加载 ASR 模型...")
            await asyncio.to_thread(asr_engine.load)
            logger.info("ASR 就绪")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ASR 加载失败，将以无 ASR 模式运行: %s", exc)

    async with LLMClient(llm_cfg) as llm, TTSClient(tts_cfg) as tts:
        ok, info = await tts.health()
        if not ok:
            logger.error(
                "本地 TTS 服务不可用: %s\n"
                "请先启动: wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/serve_tts_wsl.sh",
                info,
            )
            return 2
        logger.info("TTS 就绪: device=%s", info.get("device"))

        server = DialogueServer(
            cfg, llm_client=llm, tts_client=tts, card=card, asr_engine=asr_engine
        )
        await server.serve()
    return 0


def main() -> int:
    from pathlib import Path

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    root = Path(__file__).resolve().parent.parent.parent
    ap = argparse.ArgumentParser(description="WebSocket 对话服务端")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址（默认仅回环，改成 0.0.0.0 才能被开发板访问）")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--in-sample-rate", type=int, default=16000)
    ap.add_argument("--in-channels", type=int, default=4)
    ap.add_argument("--token", default="", help="设备访问令牌（空表示不校验）")
    ap.add_argument("--character-file", default=str(root / "server" / "characters" / "default.yaml"))
    ap.add_argument("--env", default=str(root / ".env"))
    ap.add_argument("--no-asr", action="store_true", help="不加载 ASR（仅测协议）")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())