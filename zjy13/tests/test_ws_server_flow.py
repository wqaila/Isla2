"""WebSocket 服务端主流程测试。

背景：``server/ws/server.py`` 是整条板子链路的核心（900+ 行），
但此前只有 ``test_ws_server_voice.py`` 里 3 个 ``voice_id`` 用例，
**协议时序全靠手工跑模拟器验证**。本文件用替身把关键行为锁住：

1. 多轮上下文：整条连接复用同一个 ``ConversationSession``
   （修之前每轮新建，板子上完全没有记忆）
2. 播放看门狗：等不到 ``playback_done`` 也能自动回 IDLE
3. 连接数上限
4. 2 通道上行不能丢掉第二支麦克风
"""

from __future__ import annotations

import asyncio
import types
from pathlib import Path

import numpy as np
import pytest

from server.dialogue.character import CharacterCard
from server.tts import TTSConfig, TTSResult
from server.ws.audio import AudioFormat, TurnAudio, voice_channel_indices
from server.ws.server import (
    PLAYBACK_MIN_TIMEOUT_S,
    ClientSession,
    DialogueServer,
    ServerConfig,
)
from server.ws.states import State

CHAR_DIR = Path(__file__).resolve().parent.parent / "server" / "characters"


# ----------------------------------------------------------------------
# 替身
# ----------------------------------------------------------------------


class FakeWS:
    """最小 WebSocket 替身，只记录发出的内容。"""

    def __init__(self) -> None:
        self.sent: list[object] = []
        self.closed = False
        self.remote_address = ("127.0.0.1", 12345)

    async def send(self, data: object) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True

    def json_messages(self) -> list[dict]:
        import json

        return [json.loads(x) for x in self.sent if isinstance(x, str)]


class FakeLLM:
    """记录每次 chat 收到的消息条数，用于验证历史是否累积。"""

    def __init__(self) -> None:
        self.calls: list[int] = []

    async def chat(self, messages, *, turn_id=None, on_delta=None, **kw):
        msgs = list(messages)
        self.calls.append(len(msgs))

        class _Result:
            text = "好的呀"
            first_token_ms = 1.0
            total_ms = 2.0
            finish_reason = "stop"

        if on_delta:
            on_delta(_Result.text)
        return _Result()


class FakeTTS:
    """返回固定长度的静音 WAV，避免依赖 WSL 服务。"""

    def __init__(self, duration_s: float = 0.2) -> None:
        self.config = TTSConfig()
        self.duration_s = duration_s

    async def synthesize(self, text, *, turn_id=None, voice_id=None, **kw):
        path = Path("logs/_test_silence.wav")
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_silence_wav(path, self.duration_s)
        return TTSResult(
            audio_path=path,
            sample_rate=24000,
            duration_s=self.duration_s,
            first_audio_ms=1.0,
            total_ms=2.0,
            realtime_factor=0.01,
            text=text,
        )


def _write_silence_wav(path: Path, duration_s: float, sample_rate: int = 24000) -> None:
    import wave

    frames = int(duration_s * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * frames)


def _make_server(
    *,
    card_name: str = "elysia",
    llm: FakeLLM | None = None,
    tts: FakeTTS | None = None,
    config: ServerConfig | None = None,
) -> tuple[DialogueServer, FakeLLM]:
    card = CharacterCard.from_file(CHAR_DIR / f"{card_name}.yaml")
    fake_llm = llm or FakeLLM()
    server = DialogueServer(
        config or ServerConfig(),
        llm_client=fake_llm,
        tts_client=tts or FakeTTS(),
        card=card,
    )
    # ASR 用替身，避免加载真实模型
    server.asr = object()
    server._transcribe_bytes = lambda wav: "我叫阿哲，在做嵌入式开发"
    return server, fake_llm


def _fake_audio(seconds: float = 0.5, channels: int = 2) -> TurnAudio:
    fmt = AudioFormat(sample_rate=16000, channels=channels)
    audio = TurnAudio(format=fmt)
    samples = int(16000 * seconds) * channels
    audio.append(0, np.zeros(samples, dtype="<i2").tobytes())
    return audio


async def _run_one_turn(server: DialogueServer, client: ClientSession) -> None:
    """把 client 摆到「刚录完音」的状态，跑一轮完整流水线。"""
    client.state.reset()
    client.state.to(State.RECORDING)
    client.new_turn()
    client.audio = _fake_audio()
    await server._run_pipeline(client, client.turn_id, 0.0)


# ----------------------------------------------------------------------
# 1. 多轮上下文（本轮修复的核心缺陷）
# ----------------------------------------------------------------------


def test_dialogue_session_is_reused_across_turns() -> None:
    """整条连接必须复用同一个会话，否则板子上没有多轮记忆。

    修之前 ``_run_pipeline`` 里每轮 ``ConversationSession(card, llm)`` 新建，
    第二轮发给 LLM 的消息只有 system + user（历史为空），
    问「你记得我刚才说什么吗」会被 guards 按空历史拦截。
    """
    server, fake_llm = _make_server()
    client = ClientSession(ws=FakeWS(), session_id="sess-reuse")

    async def scenario() -> None:
        await _run_one_turn(server, client)
        await _run_one_turn(server, client)

    asyncio.run(scenario())

    assert len(fake_llm.calls) == 2
    first, second = fake_llm.calls
    # 第 2 轮必须比第 1 轮多带 2 条历史（user + assistant）
    assert second == first + 2, (
        f"第 2 轮消息数 {second} 未比第 1 轮 {first} 多，说明历史没有累积"
    )


def test_dialogue_session_created_lazily() -> None:
    """会话应是懒创建的，且同一 client 拿到的始终是同一个对象。"""
    server, _ = _make_server()
    client = ClientSession(ws=FakeWS(), session_id="sess-lazy")
    assert client.dialogue is None
    first = server._dialogue_for(client)
    second = server._dialogue_for(client)
    assert first is second
    assert client.dialogue is first


def test_cleanup_drops_dialogue_history() -> None:
    """断开连接要释放历史，避免长期运行累积内存。"""
    server, _ = _make_server()
    ws = FakeWS()
    client = ClientSession(ws=ws, session_id="sess-clean")
    server._clients[client.session_id] = client
    server._dialogue_for(client)
    assert client.dialogue is not None

    asyncio.run(server._cleanup(client))
    assert client.dialogue is None
    assert client.session_id not in server._clients


# ----------------------------------------------------------------------
# 2. 播放看门狗
# ----------------------------------------------------------------------


def test_playback_timer_armed_after_audio_sent() -> None:
    """进入 PLAYING 后必须挂上定时器。"""
    server, _ = _make_server()
    client = ClientSession(ws=FakeWS(), session_id="sess-timer")

    async def scenario() -> None:
        await _run_one_turn(server, client)
        assert client.state.state == State.PLAYING
        assert client.playback_timer is not None
        assert not client.playback_timer.done()

    try:
        asyncio.run(scenario())
    finally:
        # 清理掉定时器，避免 pytest 结束时报 pending task
        if client.playback_timer and not client.playback_timer.done():
            client.playback_timer.cancel()


def test_playback_done_cancels_timer() -> None:
    """正常收到 playback_done 后定时器必须撤掉。"""
    server, _ = _make_server()
    ws = FakeWS()
    client = ClientSession(ws=ws, session_id="sess-done")

    async def scenario() -> None:
        await _run_one_turn(server, client)
        await server._on_playback_done(
            client, {"received_bytes": client.expect_audio_bytes}
        )
        assert client.state.state == State.IDLE
        assert client.playback_timer is None

    asyncio.run(scenario())


def test_watchdog_forces_idle_when_no_playback_done() -> None:
    """板子不回 playback_done 时，看门狗必须把状态强制拉回 IDLE。

    这是 WORKLOG-2026-11-04 第 3 条「断连后状态残留、需 VOICE STOP 复位」
    的自动恢复实现。
    """
    server, _ = _make_server()
    ws = FakeWS()
    client = ClientSession(ws=ws, session_id="sess-wd")
    client.state.reset()
    client.state.to(State.RECORDING)
    client.state.to(State.ASR_PROCESSING)
    client.state.to(State.LLM_PROCESSING)
    client.state.to(State.TTS_PROCESSING)
    client.state.to(State.PLAYING)
    client.turn_id = "t-wd"
    client.expect_audio_bytes = 100
    client.expect_audio_frames = 1

    async def scenario() -> None:
        # 直接跑看门狗主体，把超时压到 0.05s，避免测试等 20 秒
        await server._playback_watchdog(client, "t-wd", 0.05)
        assert client.state.state == State.IDLE
        assert client.expect_audio_bytes == 0
        reasons = [
            m.get("reason") for m in ws.json_messages() if m.get("type") == "turn_done"
        ]
        assert "playback_timeout" in reasons

    asyncio.run(scenario())


def test_watchdog_ignores_newer_turn() -> None:
    """看门狗触发时若已进入新一轮，不得误杀。"""
    server, _ = _make_server()
    client = ClientSession(ws=FakeWS(), session_id="sess-new")
    client.state.to(State.RECORDING)
    client.turn_id = "t-new"

    async def scenario() -> None:
        await server._playback_watchdog(client, "t-old", 0.01)
        # 状态不变、没发 turn_done
        assert client.state.state == State.RECORDING

    asyncio.run(scenario())


def test_playback_timeout_bounds() -> None:
    """看门狗超时必须 ≥ 播放时长 + 余量，且不小于最小值。"""
    from server.ws.server import PLAYBACK_GRACE_S

    assert PLAYBACK_GRACE_S > 0
    assert PLAYBACK_MIN_TIMEOUT_S >= 10


# ----------------------------------------------------------------------
# 3. 连接数上限
# ----------------------------------------------------------------------


def test_connection_limit_rejects_extra_client() -> None:
    """超过 max_connections 时应拒绝并关闭连接。"""
    server, _ = _make_server(config=ServerConfig(max_connections=1))

    async def scenario() -> None:
        first = FakeWS()
        # 占住一个名额
        server._clients["existing"] = ClientSession(ws=first, session_id="existing")

        second = FakeWS()
        await server.handler(second)
        assert second.closed is True
        codes = [
            m.get("code") for m in second.json_messages() if m.get("type") == "error"
        ]
        assert "too_many_connections" in codes

    asyncio.run(scenario())


# ----------------------------------------------------------------------
# 4. 上行声道降混
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "channels,expected",
    [
        (2, [0, 1]),          # 固件实际配置：双麦，两支都要用
        (4, [1, 2, 3]),       # RMNM：跳过 0 号回采
        (1, [0]),
        (3, [0, 1, 2]),
    ],
)
def test_voice_channel_indices(channels: int, expected: list[int]) -> None:
    assert voice_channel_indices(channels) == expected


def test_two_channel_audio_uses_both_mics() -> None:
    """2 通道时不能用 4 通道布局筛通道（那会只剩 [1]，丢掉一半信号）。"""
    fmt = AudioFormat(sample_rate=16000, channels=2)
    audio = TurnAudio(format=fmt)
    # 左声道全 1000、右声道全 -1000 → 两路都参与时均值为 0；
    # 只用右路则得到 -1000/32768，两者明显可区分。
    frame = np.array([1000, -1000], dtype="<i2")
    audio.append(0, np.tile(frame, 1600).tobytes())

    mono = audio.to_mono_16k()
    assert mono.size == 1600
    assert abs(float(np.abs(mono).max())) < 1e-3, (
        "两路未同时参与降混（结果应相互抵消接近 0）"
    )


def test_four_channel_audio_skips_reference() -> None:
    """4 通道时 0 号回采通道必须被排除。"""
    fmt = AudioFormat(sample_rate=16000, channels=4)
    audio = TurnAudio(format=fmt)
    # 0 号通道给满幅值，其它给 0；若回采参与降混，结果会明显非零
    frame = np.array([30000, 0, 0, 0], dtype="<i2")
    audio.append(0, np.tile(frame, 800).tobytes())

    mono = audio.to_mono_16k()
    assert float(np.abs(mono).max()) == pytest.approx(0.0, abs=1e-6)


# ----------------------------------------------------------------------
# 5. 收尾一致性
# ----------------------------------------------------------------------


def test_finish_turn_emits_turn_done() -> None:
    server, _ = _make_server()
    ws = FakeWS()
    client = ClientSession(ws=ws, session_id="sess-fin")
    client.state.to(State.RECORDING)
    client.turn_id = "t-fin"

    asyncio.run(server._finish_turn(client, reason="unit_test"))
    msgs = ws.json_messages()
    done = [m for m in msgs if m.get("type") == "turn_done"]
    assert done and done[-1]["reason"] == "unit_test"
    assert client.state.state == State.IDLE
