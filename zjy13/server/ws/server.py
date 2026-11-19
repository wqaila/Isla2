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
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import websockets

from ..config import LLMConfig, load_env_file
from ..dialogue.character import CharacterCard
from ..dialogue.session import ConversationSession
from ..llm import LLMClient, LLMError
from ..protocol import (
    ASR_FINAL,
    CANCEL,
    DEVICE_HELLO,
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
)
from ..tts import TTSClient, TTSConfig, TTSError, TTSUnavailableError
from .audio import AudioFormat, TurnAudio, encode_pcm16_wav
from .states import DialogueStateMachine, IllegalTransitionError, State

logger = logging.getLogger("ws.server")

#: 心跳超时（秒）——超过则断开，V2 第十一节要求连接管理
HEARTBEAT_TIMEOUT_S = 30.0

#: 单轮处理总超时（秒）。
#:
#: 2026-11-19 从 60 调到 120。原因：这个超时包住 ASR + LLM + TTS + 下发，
#: 而 zjy14 侧的**推理队列等待上限就是 60s**（configs/gpu.yaml 的
#: queue_timeout_s）。两者都是 60 时，队列一拥塞，zjy14 独自就能吃掉
#: zjy13 的全部预算（还没算 ASR/LLM/合成时间），结果是 zjy13 先抛
#: 「本轮处理超时」，而 zjy14 那边仍在排队并继续占着并发槽位 ——
#: 双方都做了正确的事，却因为预算相等而必然打架。
#:
#: 取 120 的逻辑：必须 > 队列等待(60) + 一次合成的最坏耗时。
#: 实测单次合成长句约 5s，留足余量后 120 是安全的。
TURN_TIMEOUT_S = 120.0

#: 播放看门狗：在「音频时长 + 该余量」内没收到 playback_done 就强制收尾。
#:
#: 为什么必须有：``_run_pipeline`` 发完音频就返回，``TURN_TIMEOUT_S`` 的
#: ``asyncio.timeout`` 也随之失效。若开发板因断连/卡死没回 playback_done，
#: 状态会永久停在 PLAYING，之后所有 recording_start 都被判 turn_busy，
#: 只能人工发 ``VOICE STOP`` 复位（WORKLOG-2026-11-04 第 3 条已知限制）。
PLAYBACK_GRACE_S = 15.0

#: 看门狗最小超时（短音频也要留足播放 + 上报的时间）
PLAYBACK_MIN_TIMEOUT_S = 20.0

#: 流式播放时长度未知，看门狗先用这个估计值。
#: 流式下真正的收尾靠板子收到结束标记帧后回报 playback_done；
#: 这个超时只兜底「板子彻底失联」。取值要大一些，避免长回复被误杀。
PLAYBACK_MAX_GUESS_S = 120.0

#: 延迟自适应：记录最近 N 轮的「松手→首块音频」耗时，
#: 用于决定要不要播填充音。
#:
#: 为什么需要：如果上一轮很快（比如缓存命中，1 秒内就出声），
#: 再播 0.7 秒的「嗯…」反而显得啰嗦。
#: 只有当历史延迟明显超过填充音长度时才播。
LATENCY_HISTORY_SIZE = 5

#: 历史平均延迟低于这个值时跳过填充音（秒）。
#: 取 1.2s：填充音本身约 0.7s，若等待不足 1.2s，
#: 播填充音会让总时长变长，得不偿失。
FILLER_SKIP_BELOW_S = 1.2


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
    #: 播放看门狗任务：PLAYING 状态下等不到 playback_done 时强制收尾
    playback_timer: asyncio.Task | None = None
    #: 填充音发送任务（「嗯…」等语气词）。正文就绪或本轮失败时要取消它，
    #: 否则会在后台继续发音频帧，污染下一轮的播放。
    filler_task: asyncio.Task | None = None
    #: 通知填充音任务「正文已就绪，立刻停」
    filler_body_ready: asyncio.Event | None = None
    #: 通知填充音任务「本轮已作废，立刻停」
    filler_cancel: asyncio.Event | None = None
    #: 本轮填充音是否已开过播放会话（正文要接在它后面，不再发 playback_start）
    filler_session_open: bool = False
    last_heartbeat: float = field(default_factory=time.perf_counter)
    device_id: str = ""
    hello_done: bool = False
    #: 本轮应下发的音频字节数与帧数（用于 playback_done 校验）
    expect_audio_bytes: int = 0
    expect_audio_frames: int = 0
    #: 整条连接共用的对话会话。
    #:
    #: 必须挂在连接上复用，不能每轮新建：``ConversationSession`` 持有历史，
    #: 每轮 new 一个等于"每轮都是全新对话"，板子上问"你记得我刚才说什么吗"
    #: 会被 ``guards.py`` 按空历史拦截，答"我们刚开始聊"。
    #: （终端 dialogue_loop / 网页 demo 都是长会话，只有 WS 链路曾漏掉这点。）
    dialogue: ConversationSession | None = None

    # ---- 监控用字段（2026-11-18 新增，只读展示，不参与业务逻辑）----
    #: 连接建立时间（perf_counter 基准，用于算已连接时长）
    connected_at: float = field(default_factory=time.perf_counter)
    #: 对端地址字符串，如 "192.168.137.42:51234"
    peer_str: str = ""
    #: 本轮各阶段耗时（毫秒），未完成的为 None
    timings: dict[str, float | None] = field(
        default_factory=lambda: {
            "asr_ms": None,
            "llm_first_ms": None,
            "llm_total_ms": None,
            "tts_first_ms": None,
            "playback_ms": None,
        }
    )
    #: 本轮已发送的音频字节数与帧数（用于进度展示）
    sent_bytes: int = 0
    sent_frames: int = 0
    #: 最近 N 条错误（环形缓冲，最新的在后）
    recent_errors: list[dict[str, Any]] = field(default_factory=list)
    #: 本轮 ASR 识别出的文本（监控展示用）
    last_asr_text: str = ""
    #: 本轮 LLM 回复文本（监控展示用）
    last_reply_text: str = ""
    #: 已完成的对话轮数（本连接内）
    turns_done: int = 0
    #: 本连接内已开过多少轮。用于生成单调递增的 turn_id。
    #:
    #: 为什么需要（2026-11-19 修复）：原来 turn_id 用
    #: `int(time.time()*1000) % 100000` —— 毫秒对 100000 取模，
    #: **每 100 秒就会重复一次**。会话活得久一点，两轮就会拿到同一个
    #: turn_id，进而：
    #:   1. TTSClient 的 `_completed_turns` 判重会误拒新的一轮
    #:   2. turn_id 的 CRC32（turn_id_short）相同，固件会把两轮当成同一轮
    #:      而丢弃新帧（app_protocol.c 的轮次校验）
    #: 改用会话内递增序号，天然单调、绝不重复。
    turn_counter: int = 0

    def next_seq(self) -> int:
        self.turn_seq += 1
        return self.turn_seq

    def new_turn(self) -> str:
        self.turn_seq = 0
        self.turn_counter += 1
        # turn_id 只需在**本连接内**唯一（去重与固件轮次校验都是按会话看的），
        # 所以 `会话id-t序号` 就够了，不必掺时间戳。
        # 保留 `t` 前缀是为了兼容既有日志格式与监控页展示。
        self.turn_id = f"{self.session_id}-t{self.turn_counter}"
        # 新一轮：重置本轮计时与计数，但保留 recent_errors 与 turns_done
        self.timings = {
            "asr_ms": None,
            "llm_first_ms": None,
            "llm_total_ms": None,
            "tts_first_ms": None,
            "playback_ms": None,
        }
        self.sent_bytes = 0
        self.sent_frames = 0
        self.last_asr_text = ""
        self.last_reply_text = ""
        return self.turn_id

    def record_error(self, code: str, message: str, *, keep: int = 10) -> None:
        """记录一条错误（环形缓冲，最多保留 keep 条）。"""
        self.recent_errors.append({
            "code": code,
            "message": message,
            "at": time.strftime("%H:%M:%S"),
            "turn_id": self.turn_id or None,
        })
        if len(self.recent_errors) > keep:
            del self.recent_errors[:-keep]


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
        voice_id: str | None = None,
    ) -> None:
        self.config = config
        self.llm = llm_client
        self.tts = tts_client
        self.card = card
        self.asr = asr_engine
        # 音色必须跟着角色卡走，否则开发板听到的声音和终端演示不一致。
        # 优先取角色卡绑定的 tts_voice_id，其次回退到 TTSConfig 的默认音色。
        tts_settings = card.tts_settings() or {}
        self.voice_id = (
            voice_id
            or tts_settings.get("tts_voice_id")
            or tts_client.config.default_voice
        )
        # 语速同理：角色卡写了 speaking_rate 就必须真的生效。
        # docs/character-review.md 缺陷 4 要求「角色卡里 TTS 不支持的参数
        # 必须明确提示或忽略，不能声称已生效」——此前只是透传，从未传给 TTS。
        rate = tts_settings.get("speaking_rate")
        self.speaking_rate = float(rate) if isinstance(rate, (int, float)) else 1.0
        self._clients: dict[str, ClientSession] = {}

        # ---- 监控计数器（2026-11-18，只读展示）----
        #: 服务启动时间（perf_counter 基准）
        self._started_at: float = time.perf_counter()
        #: 累计完成的对话轮数
        self._total_turns: int = 0
        #: 累计错误数
        self._total_errors: int = 0
        #: 因连接数超限被拒绝的次数（这些连接不会进 _clients，必须单独计数）
        self._rejected_connections: int = 0
        #: 累计接受过的连接数（含已断开）
        self._total_connections: int = 0

        # 填充音：预生成的语气词（嗯… / 这个嘛… / 唔…）。
        # 用户松手后到正文出声有约 2.3 秒静默，先播一个语气词能显著降低
        # 感知延迟。资源加载失败时自动降级为「不播填充音」，不影响主链路。
        try:
            from ..tts.fillers import FillerBank

            self.fillers = FillerBank()
        except Exception as exc:  # noqa: BLE001
            logger.warning("填充音加载失败，将不播语气词: %s", exc)
            self.fillers = None

        # 延迟自适应：记录最近若干轮的「松手→首块音频」耗时（秒）。
        # 若历史平均很快，就跳过填充音（否则显得啰嗦）。
        self._latency_samples: list[float] = []

    # ------------------------------------------------------------------
    # 延迟自适应
    # ------------------------------------------------------------------

    def _record_latency(self, seconds: float) -> None:
        """记录一轮的「松手→首块音频」耗时。"""
        self._latency_samples.append(seconds)
        if len(self._latency_samples) > LATENCY_HISTORY_SIZE:
            self._latency_samples.pop(0)

    def _mean_latency(self) -> float | None:
        """最近若干轮的平均延迟（秒）。样本不足时返回 None。"""
        if not self._latency_samples:
            return None
        return sum(self._latency_samples) / len(self._latency_samples)

    def _should_play_filler(self) -> bool:
        """是否该播填充音。

        规则：
        - 没有填充音资源 → 不播
        - 还没有历史样本（第一轮）→ 播（首轮必须给用户即时反馈）
        - 历史平均延迟 >= FILLER_SKIP_BELOW_S → 播（等待够久，填充有意义）
        - 历史平均很快 → 跳过（播了反而让总时长变长）
        """
        if self.fillers is None or not self.fillers.available:
            return False
        mean = self._mean_latency()
        if mean is None:
            return True
        return mean >= FILLER_SKIP_BELOW_S

    # ------------------------------------------------------------------
    # 监控快照（只读，供 Web 端展示）
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """导出当前运行时状态，供监控 Web 端展示。

        约束（必须遵守，否则监控页会变成故障源）：
          1. **只读**：不修改任何状态，不创建任务，不发送消息
          2. **不阻塞**：纯内存读取，无 IO、无 await
          3. **不抛异常**：单个 client 出错不能影响其它 client，
             也不能让整个快照失败
          4. **字段缺失给默认值**，不用 None 冒充 0

        时间说明：``last_heartbeat`` 与 ``connected_at`` 用
        ``time.perf_counter()`` 基准，这里统一换算成「距今多少秒」，
        避免前端拿到不可比的绝对时间戳。
        """
        now = time.perf_counter()

        clients: list[dict[str, Any]] = []
        for client in list(self._clients.values()):
            try:
                clients.append(self._client_snapshot(client, now))
            except Exception as exc:  # noqa: BLE001
                # 单个 client 出错不影响其它；给出错误占位
                logger.exception(
                    "生成 client 快照失败 session=%s", client.session_id
                )
                clients.append({
                    "session_id": getattr(client, "session_id", "?"),
                    "error": f"快照生成失败: {exc}",
                })

        return {
            "server": {
                "host": self.config.host,
                "port": self.config.port,
                "character_id": self.card.character_id,
                "character_name": self.card.name,
                "uptime_s": round(now - self._started_at, 1),
                "max_connections": self.config.max_connections,
                "access_token_required": bool(self.config.access_token),
                "audio_in_sample_rate": self.config.audio_in_sample_rate,
                "audio_in_channels": self.config.audio_in_channels,
                "audio_out_sample_rate": self.config.audio_out_sample_rate,
                "protocol_version": PROTOCOL_VERSION,
                "filler_available": bool(
                    self.fillers is not None and getattr(self.fillers, "available", False)
                ),
                "mean_latency_s": (
                    round(m, 3) if (m := self._mean_latency()) is not None else None
                ),
            },
            "clients": clients,
            "counters": {
                "active": len(self._clients),
                "total_connections": self._total_connections,
                "rejected_connections": self._rejected_connections,
                "total_turns": self._total_turns,
                "total_errors": self._total_errors,
            },
        }

    def _client_snapshot(self, client: ClientSession, now: float) -> dict[str, Any]:
        """单个连接的快照。由 snapshot() 调用，异常由调用方兜底。"""
        st = client.state
        heartbeat_age = now - client.last_heartbeat
        connected_s = now - client.connected_at

        # 音频进度：流式下 expect_frames 为 0（总长未知），此时不给百分比
        expect_frames = client.expect_audio_frames
        expect_bytes = client.expect_audio_bytes
        if expect_frames > 0:
            progress: float | None = round(
                min(1.0, client.sent_frames / expect_frames), 3
            )
        else:
            progress = None

        return {
            "session_id": client.session_id,
            "device_id": client.device_id or "",
            "peer": client.peer_str,
            "hello_done": client.hello_done,
            "connected_s": round(connected_s, 1),

            # 状态机快照（复用 DialogueStateMachine.snapshot）
            "state": st.state.value,
            "state_elapsed_ms": round(st.elapsed_ms, 1),
            "turn_id": st.turn_id or client.turn_id or "",
            "turn_seq": client.turn_seq,
            "is_busy": st.is_busy,

            # 心跳
            "last_heartbeat_s": round(heartbeat_age, 1),
            "heartbeat_ok": heartbeat_age <= HEARTBEAT_TIMEOUT_S,

            # 音频下发进度
            "audio": {
                "expect_bytes": expect_bytes,
                "expect_frames": expect_frames,
                "sent_bytes": client.sent_bytes,
                "sent_frames": client.sent_frames,
                "progress": progress,
                "streaming": expect_frames == 0,
            },

            # 填充音与对话
            "filler_session_open": client.filler_session_open,
            "dialogue_turns": client.turns_done,
            "turns_done": client.turns_done,

            # 本轮文本
            "last_asr_text": client.last_asr_text,
            "last_reply_text": client.last_reply_text,

            # 阶段耗时
            "timings": dict(client.timings),

            # 错误
            "last_error": st.last_error,
            "errors_recent": list(client.recent_errors[-5:]),
        }

    def _dialogue_for(self, client: ClientSession) -> ConversationSession:
        """取本连接共用的对话会话，没有就建一个。
        关键：**整条 WebSocket 连接复用同一个 session**，历史才能跨轮累积。
        此前每轮 ``ConversationSession(self.card, self.llm)`` 新建，
        导致板子上完全没有多轮上下文（终端与网页演示则是正常的）。
        """
        if client.dialogue is None:
            client.dialogue = ConversationSession(self.card, self.llm)
        return client.dialogue

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
        # 监控：记录错误（供 Web 端展示）
        self._total_errors += 1
        client.record_error(str(code), message)
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
            # 监控：累计本轮已发送量（结束标记帧 payload 为空，不计入）
            if pcm:
                client.sent_bytes += len(pcm)
                client.sent_frames += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("发送音频失败: %s", exc)

    # ------------------------------------------------------------------
    # 填充音（语气词）
    # ------------------------------------------------------------------

    async def _interrupt_for_new_turn(
        self, client: ClientSession, *, reason: str
    ) -> None:
        """打断当前轮，为新一轮让路。

        与 ``_on_cancel`` 的区别：
        - ``_on_cancel`` 是客户端主动发 ``cancel``，本轮作废、不发 ``turn_done``
        - 本方法是「用户直接开口说话」触发的隐式打断：要发 ``turn_cancelled``
          让板子知道上一轮作废（否则板子还在等 playback_done），
          但**不发 turn_done**（新一轮马上开始，板子不需要回 IDLE）。

        清理内容：
        1. 停填充音（否则后台继续发音频帧污染新一轮）
        2. 取消本轮的 ASR/LLM/TTS 处理任务
        3. 撤掉播放看门狗
        4. 清空本轮音频缓冲
        """
        turn_id = client.turn_id
        await self._stop_filler(client)
        self._cancel_playback_timer(client)

        if client.task is not None and not client.task.done():
            client.task.cancel()
            try:
                await client.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        client.task = None

        client.audio = None
        client.expect_audio_bytes = 0
        client.expect_audio_frames = 0

        # 通知板子：上一轮作废，别再等它的 playback_done
        await self.send_json(
            client,
            Envelope(
                type=TURN_CANCELLED,
                session_id=client.session_id,
                turn_id=turn_id,
                payload={"reason": reason},
            ).to_dict(),
        )

        # 状态复位到 IDLE，随后 _on_recording_start 会转到 RECORDING
        client.state.reset()
        logger.info("已打断 turn=%s（%s），准备接受新录音", turn_id, reason)

    async def _stop_filler(self, client: ClientSession) -> None:
        """停止本轮填充音任务。

        两种触发：正文已就绪（正常切换）、本轮失败/取消（丢弃）。
        用 ``filler_cancel`` 通知任务自己退出，而不是直接 ``task.cancel()``——
        后者会在任意 await 点抛出 CancelledError，可能让 send_audio 半途而废
        留下不完整的帧；用事件让它在帧边界退出更干净。
        """
        if client.filler_cancel is not None:
            client.filler_cancel.set()
        task = client.filler_task
        client.filler_task = None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        client.filler_body_ready = None
        client.filler_cancel = None
        client.filler_session_open = False

    async def _start_playback_session(
        self,
        client: ClientSession,
        my_turn: str,
        sample_rate: int,
        *,
        reason: str,
    ) -> None:
        """开始一个流式播放会话（发 playback_start，frames=0）。

        ``reason="filler"`` 时进入 FILLER 状态（占位播放中，主流程继续）；
        ``reason="body"`` 时进入 PLAYING 状态（正文播放，本轮即将结束）。
        """
        client.state.to(State.FILLER if reason == "filler" else State.PLAYING)
        self._arm_playback_timer(client, my_turn, PLAYBACK_MAX_GUESS_S)
        client.expect_audio_bytes = 0
        client.expect_audio_frames = 0
        await self.send_json(
            client,
            Envelope(
                type=PLAYBACK_START,
                session_id=client.session_id,
                turn_id=my_turn,
                seq=client.next_seq(),
                payload={
                    "sample_rate": sample_rate,
                    "duration_s": 0,
                    "bytes": 0,
                    "frames": 0,
                    "frame_bytes": 4096,
                    "streaming": True,
                },
            ).to_dict(),
        )
        logger.info(
            "流式播放开始 turn=%s 采样率=%d 原因=%s",
            my_turn, sample_rate, reason,
        )

    async def _send_fillers(
        self,
        client: ClientSession,
        my_turn: str,
        *,
        body_ready: asyncio.Event,
        cancel: asyncio.Event,
    ) -> tuple[int, int, int]:
        """边播边补地发送填充音，直到正文就绪 / 取消 / 达上限。

        ## 「边播边补」必须真的跟着播放进度

        2026-11-19 修复：原实现虽然写着"边播边补"，实际是**一口气预填满**。

        原因：循环只看 ``session.should_continue()``（累计时长 < 3s），
        而 ``send_audio`` 对填充音**没有节流** —— 一个 0.7s 的填充音
        = 9 帧 × 4096B，几毫秒内全部写进 socket。于是：

          发第 1 声 → sent_seconds=0.70 → 条件仍成立 → 发第 2 声 → ...
          → 直到累计 2.87s 才停

        板子那边要花 2.9 秒才播完，用户听到「嗯…」「这个嘛…」「唔…」
        「嗯…」连着四声，非常不自然（实测日志每轮 4~5 声）。

        修法：发完一个填充音后，按**它自己的时长**等待，让发送节奏
        与播放节奏对齐。这样正文一就绪，板子缓冲里最多只剩正在播的
        那一个，不会积压。

        注意不能用 ``asyncio.sleep`` 死等满一个填充音：正文可能中途到达，
        必须能被 ``body_ready`` / ``cancel`` 立刻打断，所以用
        ``asyncio.wait`` 等「超时」或「事件」两者之一。
        """
        from ..tts.fillers import FillerSession

        if self.fillers is None or not self.fillers.available:
            return 0, 0, 0

        session = FillerSession(self.fillers)
        seq = 0
        sent_bytes = 0
        count = 0
        step = 4096

        while session.should_continue():
            if body_ready.is_set() or cancel.is_set():
                break
            clip = session.next_clip()
            if clip is None:
                break
            # 按帧切分下发（板子协议单帧上限 48KB）
            for off in range(0, len(clip.pcm), step):
                if body_ready.is_set() or cancel.is_set():
                    # 正文到了：剩下的填充音立刻丢弃，让板子尽快切正文
                    break
                await self.send_audio(client, clip.pcm[off:off + step], seq)
                seq += 1
                sent_bytes += min(step, len(clip.pcm) - off)
            count += 1
            logger.debug(
                "填充音 %s (%.2fs) 已发，累计 %.2fs，seq 到 %d",
                clip.id, clip.duration_s, session.sent_seconds, seq,
            )
            # 关键：等这一声播完再考虑下一声。
            # 若正文/取消先到则立刻退出，不必等满。
            if session.should_continue():
                await self._wait_filler_played(
                    clip.duration_s, body_ready=body_ready, cancel=cancel
                )

        return count, sent_bytes, seq

    @staticmethod
    async def _wait_filler_played(
        duration_s: float,
        *,
        body_ready: asyncio.Event,
        cancel: asyncio.Event,
    ) -> None:
        """等待填充音「播完」或「正文就绪 / 取消」，先到者胜。

        用 asyncio.wait 而不是 sleep，是为了让正文一到达就立刻打断等待，
        不必空等满一个填充音的时长（否则首字延迟会被人为拉长）。
        """
        if duration_s <= 0:
            return
        waiters = [
            asyncio.create_task(body_ready.wait()),
            asyncio.create_task(cancel.wait()),
        ]
        try:
            await asyncio.wait(
                waiters, timeout=duration_s, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            # 没完成的必须取消，否则会留下悬挂任务（asyncio 会报
            # "Task was destroyed but it is pending"）。
            for t in waiters:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    # ------------------------------------------------------------------
    # 连接生命周期
    # ------------------------------------------------------------------

    async def handler(self, ws: Any) -> None:
        session_id = uuid.uuid4().hex[:12]
        peer = getattr(ws, "remote_address", None)

        # 连接数上限（docs/protocol.md 第九节写了「最大连接数 4（可配）」，
        # 此前 ServerConfig.max_connections 定义后从未校验，属于文档承诺未实现）。
        if len(self._clients) >= self.config.max_connections:
            self._rejected_connections += 1
            logger.warning(
                "连接数超限 %d/%d，拒绝 session=%s peer=%s",
                len(self._clients), self.config.max_connections, session_id, peer,
            )
            try:
                await ws.send(json.dumps(
                    make_error(
                        "too_many_connections",
                        f"连接数已达上限 {self.config.max_connections}",
                        fatal=True,
                    ),
                    ensure_ascii=False,
                ))
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
            return

        client = ClientSession(ws=ws, session_id=session_id)
        # peer 在 websockets 里是 (host, port) 元组。
        # 直接 str(peer) 会得到 "('192.168.137.254', 56953)" ——
        # 前端展示很难看，所以这里格式化成 "ip:port"。
        if isinstance(peer, (tuple, list)) and len(peer) >= 2:
            client.peer_str = f"{peer[0]}:{peer[1]}"
        else:
            client.peer_str = str(peer) if peer else ""
        self._clients[session_id] = client
        self._total_connections += 1
        logger.info("连接建立 session=%s peer=%s", session_id, peer)

        # 心跳超时监控（协议第九节：30 秒无消息则断开）
        watchdog = asyncio.create_task(self._heartbeat_watchdog(client))
        try:
            await self._recv_loop(client)
        except websockets.exceptions.ConnectionClosed:
            logger.info("连接关闭 session=%s", session_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("连接异常 session=%s: %s", session_id, exc)
        finally:
            if not watchdog.done():
                watchdog.cancel()
                try:
                    await watchdog
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            await self._cleanup(client)

    async def _heartbeat_watchdog(self, client: ClientSession) -> None:
        """超时未收到任何消息就断开连接。

        注意：``last_heartbeat`` 在 ``_recv_loop`` 里对**每条消息**刷新
        （音频帧也算），所以活跃连接的计时器不会误杀。
        """
        interval = max(1.0, HEARTBEAT_TIMEOUT_S / 3)
        while True:
            await asyncio.sleep(interval)
            idle = time.perf_counter() - client.last_heartbeat
            if idle <= HEARTBEAT_TIMEOUT_S:
                continue
            logger.warning(
                "心跳超时 session=%s 已 %.0fs 无消息，断开连接",
                client.session_id, idle,
            )
            try:
                await client.ws.close()
            except Exception:  # noqa: BLE001
                pass
            return


    async def _cleanup(self, client: ClientSession) -> None:
        """断开时释放资源（V2 第十二节第 8 条）。"""
        self._cancel_playback_timer(client)
        if client.task and not client.task.done():
            client.task.cancel()
            try:
                await client.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        client.audio = None
        client.dialogue = None
        client.state.reset()
        self._clients.pop(client.session_id, None)
        logger.info("资源已释放 session=%s", client.session_id)

    # ------------------------------------------------------------------
    # 播放看门狗
    # ------------------------------------------------------------------

    def _cancel_playback_timer(self, client: ClientSession) -> None:
        """取消未触发的播放看门狗。"""
        timer = client.playback_timer
        client.playback_timer = None
        if timer is not None and not timer.done():
            timer.cancel()

    def _arm_playback_timer(
        self, client: ClientSession, my_turn: str, duration_s: float
    ) -> None:
        """在 PLAYING 状态下启动看门狗。

        超时仍没收到 playback_done，就认为板子侧已断连或卡死，
        主动把状态复位回 IDLE 并下发 turn_done，避免连接永久不可用。
        """
        self._cancel_playback_timer(client)
        timeout_s = max(duration_s + PLAYBACK_GRACE_S, PLAYBACK_MIN_TIMEOUT_S)
        client.playback_timer = asyncio.create_task(
            self._playback_watchdog(client, my_turn, timeout_s)
        )

    async def _playback_watchdog(
        self, client: ClientSession, my_turn: str, timeout_s: float
    ) -> None:
        try:
            await asyncio.sleep(timeout_s)
        except asyncio.CancelledError:
            return

        # 只有仍然停在 PLAYING 且还是同一轮时才强制收尾，
        # 否则说明板子已正常回报，或已进入新一轮。
        if client.state.state != State.PLAYING or client.turn_id != my_turn:
            return

        logger.warning(
            "播放看门狗触发 turn=%s %.0fs 内未收到 playback_done，强制回到 IDLE",
            my_turn, timeout_s,
        )
        client.expect_audio_bytes = 0
        client.expect_audio_frames = 0
        await self._finish_turn(client, reason="playback_timeout")


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
        # 半双工：处理中不接受新录音（V2 第九节）。
        #
        # 但 2026-11-12 起放开「打断」：正在播放（FILLER/PLAYING）时允许用户
        # 再次按下说话，先取消当前轮再接受新录音。
        # 用户明确要求允许打断——爱莉说长句时（实测可达 10 秒），
        # 只能干等说完才能插话，体验很差。
        #
        # 只在「播放相关状态」放开：ASR/LLM/TTS 处理中仍拒绝，
        # 因为那时代码正在使用 client.audio / client.task，
        # 中途切换会破坏本轮数据的完整性（而且那几段通常只持续 1~2 秒）。
        if client.state.is_busy:
            if client.state.state in (State.FILLER, State.PLAYING):
                reason = (
                    "filler_interrupt"
                    if client.state.state == State.FILLER
                    else "playback_interrupt"
                )
                logger.info(
                    "用户打断 turn=%s state=%s，取消当前轮接受新录音",
                    client.turn_id, client.state.state.value,
                )
                await self._interrupt_for_new_turn(client, reason=reason)
            else:
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
        self._cancel_playback_timer(client)
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

        # 板子已正常回报，撤销看门狗
        self._cancel_playback_timer(client)

        # 监控：本轮从进入 PLAYING 到收到 playback_done 的时长
        client.timings["playback_ms"] = round(client.state.elapsed_ms, 1)

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
        # ---- 填充音：先播「嗯…」再等正文 ----
        #
        # 2026-11-12 新增。用户从松手到听到正文要等约 2.3 秒
        # （ASR 0.1s + LLM 0.8s + TTS 首块 1.5s），这段完全静默。
        # 真人对答时会说「嗯…」「这个嘛…」，既填补空白又像在思考。
        #
        # 实现要点：
        # 1. 填充音与正文**共用同一个 playback_start 会话**，
        #    板子会把两段 PCM 当连续音频播，中间不会有播放状态切换。
        # 2. 填充音在后台任务里「边播边补」，正文首块到达时置 body_ready，
        #    填充音立刻停，后续 PCM 直接接上。
        # 3. 延迟自适应：若历史平均延迟很短，跳过填充音（否则显得啰嗦）。
        filler_count = 0
        filler_bytes = 0
        playback_session_open = False

        if self._should_play_filler():
            try:
                await self._start_playback_session(
                    client, my_turn, 24000, reason="filler"
                )
                playback_session_open = True
                client.filler_session_open = True
                client.filler_body_ready = asyncio.Event()
                client.filler_cancel = asyncio.Event()
                client.filler_task = asyncio.create_task(
                    self._send_fillers(
                        client, my_turn,
                        body_ready=client.filler_body_ready,
                        cancel=client.filler_cancel,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("填充音启动失败，继续正常流程: %s", exc)
                client.filler_task = None
                client.filler_body_ready = None
                client.filler_cancel = None
                client.filler_session_open = False
                playback_session_open = False

        # ---- ASR ----
        # 注意：填充音已把状态置为 FILLER，转换表允许 FILLER → ASR_PROCESSING。
        client.state.to(State.ASR_PROCESSING)
        audio = client.audio
        assert audio is not None
        asr_t0 = time.perf_counter()

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
        # 监控：记录 ASR 耗时与识别文本
        client.timings["asr_ms"] = round(
            (time.perf_counter() - asr_t0) * 1000, 1
        )
        client.last_asr_text = text
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

        session = self._dialogue_for(client)
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
        # 监控：记录 LLM 耗时与回复文本
        client.timings["llm_first_ms"] = record.first_token_ms
        client.timings["llm_total_ms"] = record.total_ms
        client.last_reply_text = reply

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

        # ---- TTS + 下发：流式 ----
        #
        # 2026-11-12 改造。原先的流程是「整段合成完 → 一次性下发」：
        #   TTS 合成 4.8s（8 秒音频）→ 才开始发第一帧 → 板子开播
        # 这段静默占了端到端延迟的 80%。
        #
        # 现在改成边合成边下发：
        #   首块 PCM 到达（约 1~1.5s）→ 立刻发 playback_start → 边收边发
        # 播放期间后续音频仍在合成，从而把「首字延迟」与「总合成时间」解耦。
        #
        # 协议配合：流式下总帧数未知，因此 playback_start 的 frames 传 0，
        # 板子改为在收到「负载长度为 0 的二进制帧」时判定结束。
        # 旧固件（只看 frames）在流式下不会收尾，所以服务端保留回退开关。
        step = 4096            # 4096 字节 ≈ 128ms @ 16kHz16bit
        sample_rate = 24000    # 会被实际值覆盖
        seq = 0
        sent_bytes = 0
        playback_started = False
        play_start = 0.0
        bytes_per_sec = sample_rate * 2
        lead_sec = 0.8
        # 首块不受 lead_sec 限制，一次发够「预缓冲 + 余量」。
        #
        # 为什么（2026-11-12）：固件加了 120ms 预缓冲，攒够才开始写 codec。
        # 若服务端还按 lead_sec 节流，首块可能只发出几十毫秒音频，
        # 板子就一直等不满预缓冲 —— 表现为"更晚才出声"，反而更差。
        # 这里让首块至少发够 FIRST_BURST_MS，让板子立刻能开播。
        FIRST_BURST_MS = 300
        first_burst_bytes = sample_rate * 2 * FIRST_BURST_MS // 1000
        first_pcm_ms: float | None = None
        tts_started = time.perf_counter()

        try:
            async for pcm_chunk in self.tts.synthesize_stream(
                reply,
                voice_id=self.voice_id,
                speed=self.speaking_rate,
            ):
                if not pcm_chunk:
                    continue
                if first_pcm_ms is None:
                    first_pcm_ms = (time.perf_counter() - tts_started) * 1000
                    # 监控：TTS 首块耗时
                    client.timings["tts_first_ms"] = round(first_pcm_ms, 1)

                # 第一块到手才发 playback_start：板子收到它才打开扬声器，
                # 提前发会让板子在没数据时先开功放，增加底噪与耗电。
                if not playback_started:
                    sample_rate = self.tts.last_stream_sample_rate or 24000
                    bytes_per_sec = sample_rate * 2
                    if playback_session_open:
                        # 填充音已经开过播放会话：正文直接接在后面，
                        # 只需把状态从 FILLER 切到 PLAYING，不再发 playback_start。
                        # 这样板子缓冲里「填充音 + 正文」是连续音频，无缝衔接。
                        if client.filler_body_ready is not None:
                            client.filler_body_ready.set()
                        if client.filler_task is not None:
                            try:
                                filler_count, filler_bytes, filler_seq = (
                                    await client.filler_task
                                )
                            except Exception as exc:  # noqa: BLE001
                                logger.debug("填充音任务收尾异常: %s", exc)
                                filler_seq = 0
                            client.filler_task = None
                        else:
                            filler_seq = 0
                        client.filler_session_open = False
                        client.state.to(State.PLAYING)
                        self._arm_playback_timer(
                            client, my_turn, PLAYBACK_MAX_GUESS_S
                        )
                        # seq 必须接着填充音实际用过的号往后排，
                        # 否则板子会判乱序（实测曾在 46 -> 43 处跳变）。
                        seq = filler_seq
                        logger.info(
                            "正文接入填充音会话 turn=%s 填充音=%d 个 "
                            "填充字节=%d 接续帧号=%d",
                            my_turn, filler_count, filler_bytes, seq,
                        )
                    else:
                        client.state.to(State.PLAYING)
                        self._arm_playback_timer(
                            client, my_turn, PLAYBACK_MAX_GUESS_S
                        )
                        client.expect_audio_bytes = 0    # 流式下总长未知
                        client.expect_audio_frames = 0
                        await self.send_json(
                            client,
                            Envelope(
                                type=PLAYBACK_START,
                                session_id=client.session_id,
                                turn_id=my_turn,
                                seq=client.next_seq(),
                                payload={
                                    "sample_rate": sample_rate,
                                    "duration_s": 0,
                                    "bytes": 0,
                                    "frames": 0,          # 0 = 流式，长度未知
                                    "frame_bytes": step,
                                    "streaming": True,
                                },
                        ).to_dict(),
                    )
                    play_start = time.perf_counter()
                    playback_started = True
                    logger.info(
                        "流式播放开始 turn=%s 首块=%.0fms 采样率=%d",
                        my_turn, first_pcm_ms, sample_rate,
                    )

                # 按块切帧下发，并按播放进度节流（防长音频灌爆热点链路）。
                # 首块特殊处理：不节流，一次发够 first_burst_bytes，
                # 让板子能立刻攒满预缓冲并开播（否则会等不满而延后出声）。
                for off in range(0, len(pcm_chunk), step):
                    piece = pcm_chunk[off:off + step]
                    if sent_bytes < first_burst_bytes:
                        # 首块阶段：直接发，不 sleep
                        pass
                    else:
                        played_sec = time.perf_counter() - play_start
                        sent_sec = (sent_bytes + len(piece)) / bytes_per_sec
                        ahead = sent_sec - played_sec
                        if ahead > lead_sec:
                            await asyncio.sleep(ahead - lead_sec)
                    await self.send_audio(client, piece, seq)
                    seq += 1
                    sent_bytes += len(piece)
        except TTSUnavailableError as exc:
            await self.send_error(client, ErrorCode.TTS_UNAVAILABLE, exc.message)
            await self._finish_turn(client, reason="tts_unavailable")
            return
        except TTSError as exc:
            await self.send_error(client, ErrorCode.TTS_FAILED, exc.message)
            await self._finish_turn(client, reason="tts_failed")
            return

        if not playback_started:
            await self.send_error(client, ErrorCode.TTS_FAILED, "TTS 未产生任何音频")
            await self._finish_turn(client, reason="tts_empty")
            return

        # 结束标记帧：负载长度为 0，板子据此判定收尾
        await self.send_audio(client, b"", seq)

        total_ms = (time.perf_counter() - started) * 1000
        duration_s = sent_bytes / bytes_per_sec if bytes_per_sec else 0.0
        logger.info(
            "音频已下发 turn=%s 帧=%d 字节=%d 音频=%.2fs 首块=%.0fms 端到端=%.0fms"
            "（等待 playback_done）",
            my_turn, seq, sent_bytes, duration_s,
            first_pcm_ms or 0, total_ms,
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
        # 监控：累计轮次（只有真正跑完的轮才算，被 cancel 的不算）
        if reason != "cancelled":
            client.turns_done += 1
            self._total_turns += 1
        # 先停填充音：否则它会在后台继续发音频帧，
        # 与本轮的 turn_done / 下一轮的音频混在一起。
        await self._stop_filler(client)
        # 收尾时撤掉看门狗。注意看门狗自己也会调用本函数，
        # 此时不能 cancel 当前任务（会给自己抛 CancelledError）。
        timer = client.playback_timer
        if timer is not None and timer is not asyncio.current_task():
            self._cancel_playback_timer(client)
        else:
            client.playback_timer = None
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
    # 只读 HTTP 监控端点
    # ------------------------------------------------------------------

    async def process_request(
        self, path: str, request_headers: Any
    ) -> Any:
        """在 WebSocket 端口上处理只读 HTTP 请求。

        为什么复用同一端口（2026-11-18）：
          另起 HTTP 服务要多开端口、多一个生命周期要管，
          而监控页只是几个只读 GET，复用最省事。
          ``websockets.serve`` 的 ``process_request`` 钩子正是为此设计：
          返回三元组 (status, headers, body) 表示"这是 HTTP，直接回"；
          返回 None 表示"交给 WebSocket 握手"。

        **只读**：只支持 GET，且只有两个路径。
        不提供任何写操作，避免监控页变成攻击面。

        支持路径：
          GET /api/devices          一次性快照（JSON）
          GET /api/devices/health   健康检查（极简，供探活）
          GET /api/devices/stream   SSE 流（每秒推一次快照）
        """
        import http

        # 去掉查询串，只按路径匹配
        bare = path.split("?", 1)[0].rstrip("/")

        # 非监控路径：交回 WebSocket 握手（板子连的是 ws://host:port/）
        if not bare.startswith("/api/devices"):
            return None

        # 注意：websockets 12 的 Headers 对象**不含请求方法**，
        # 只含 HTTP 头。所以不能靠它判断 GET/POST —— 早期版本这么写，
        # 导致 method 恒为空、逻辑看似正常但无法拦截 POST。
        # 正确做法：HTTP 方法不在这里判断（process_request 拿不到），
        # 只按路径响应；写操作本来就不提供，POST 到只读端点也无害。
        if bare == "/api/devices/health":
            body = json.dumps(
                {"ok": True, "active": len(self._clients)}, ensure_ascii=False
            ).encode("utf-8")
            return (
                http.HTTPStatus.OK,
                [("Content-Type", "application/json; charset=utf-8"),
                 ("Cache-Control", "no-store")],
                body,
            )

        if bare == "/api/devices/stream":
            return await self._sse_response()

        if bare == "/api/devices":
            body = json.dumps(self.snapshot(), ensure_ascii=False).encode("utf-8")
            return (
                http.HTTPStatus.OK,
                [("Content-Type", "application/json; charset=utf-8"),
                 ("Cache-Control", "no-store"),
                 # 允许 zjy14 前端（不同端口）跨域读取
                 ("Access-Control-Allow-Origin", "*")],
                body,
            )

        # /api/devices 下的未知路径
        return (
            http.HTTPStatus.NOT_FOUND,
            [("Content-Type", "application/json; charset=utf-8")],
            json.dumps(
                {
                    "error": "not_found",
                    "available": [
                        "/api/devices",
                        "/api/devices/health",
                        "/api/devices/stream",
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    async def _sse_response(self) -> Any:
        """构造 SSE 响应：每秒推一次快照。

        注意：``process_request`` 的返回值只能是「完整 body」，
        不能流式。因此 SSE 用另一个办法：返回一个带 body 的响应会一次性
        发完，不满足需求。

        所以这里**不支持** SSE，改为提示前端用轮询。
        这样做的理由：轮询 1 秒对本场景完全够用，且实现简单可靠；
        引入 SSE 需要在 ``process_request`` 之外接管连接，复杂度不值。

        返回 501 并明确告知替代方案，而不是假装支持。
        """
        import http

        return (
            http.HTTPStatus.NOT_IMPLEMENTED,
            [("Content-Type", "application/json; charset=utf-8")],
            json.dumps(
                {
                    "error": "sse_not_supported",
                    "message": "未实现 SSE。请用轮询 GET /api/devices（建议 1 秒间隔）。",
                    "poll_interval_ms": 1000,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    # ------------------------------------------------------------------

    async def serve(self) -> None:
        logger.info(
            "启动 WebSocket 服务 ws://%s:%d (字符=%s)",
            self.config.host, self.config.port, self.card.name,
        )
        logger.info(
            "监控端点已启用 http://%s:%d/api/devices（只读）",
            self.config.host, self.config.port,
        )
        async with websockets.serve(
            self.handler,
            self.config.host,
            self.config.port,
            max_size=MAX_JSON_BYTES,
            ping_interval=20,
            ping_timeout=20,
            process_request=self.process_request,
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

    # 角色卡默认值跟随 .env 的 DEFAULT_CHARACTER_ID，避免命令行入口与
    # dialogue_loop / web_demo 用不同角色（曾出现"终端是爱莉、板子是小满"）。
    from ..config import PROJECT_ROOT

    default_char = os.environ.get("DEFAULT_CHARACTER_ID", "default")
    default_card_path = PROJECT_ROOT / "server" / "characters" / f"{default_char}.yaml"
    if not default_card_path.is_file():
        default_card_path = PROJECT_ROOT / "server" / "characters" / "default.yaml"

    card = CharacterCard.from_file(args.character_file or default_card_path)

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
    ap.add_argument("--character-file", default=None,
                    help="角色卡路径（默认按 .env 的 DEFAULT_CHARACTER_ID 选择）")
    ap.add_argument("--env", default=str(root / ".env"))
    ap.add_argument("--no-asr", action="store_true", help="不加载 ASR（仅测协议）")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())