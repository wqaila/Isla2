"""设备状态快照与 HTTP 监控端点测试。

背景（2026-11-18）：新增设备状态监控 Web 端，zjy13 侧提供只读快照。
快照是唯一的数据出口 —— 它错了监控页全错，因此必须钉死。

覆盖：
1. 空 clients 时的快照结构
2. 单 client 的字段完整性
3. 多 client 互不干扰
4. 心跳换算（perf_counter -> 距今秒数）
5. 音频进度（含流式 expect_frames=0 的合法情况）
6. 状态机快照透传
7. 单个 client 出错不影响其它
8. 计数器（连接数/轮次/错误/被拒）
9. HTTP 端点（GET 快照、404、405、health）
10. 快照只读（调用后状态不变）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from server.dialogue.character import CharacterCard
from server.tts import TTSConfig, TTSResult
from server.ws.server import (
    HEARTBEAT_TIMEOUT_S,
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
    def __init__(self) -> None:
        self.sent: list[object] = []
        self.closed = False
        self.remote_address = ("192.168.137.42", 51234)

    async def send(self, data: object) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.closed = True


class FakeLLM:
    def __init__(self) -> None:
        self.reply = "嗯，我在听"

    async def chat(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        raise NotImplementedError

    async def chat_stream(self, *a, **kw):  # noqa: ANN002, ANN003, ANN201
        if False:
            yield ""
        return


class FakeTTS:
    def __init__(self) -> None:
        self.config = TTSConfig()
        self.last_stream_sample_rate = 24000

    async def synthesize_stream(self, text, **kw):  # noqa: ANN001, ANN003, ANN201
        if False:
            yield b""

    async def synthesize(self, text, **kw):  # noqa: ANN001, ANN003, ANN201
        return TTSResult(pcm=b"\x00\x00" * 100, sample_rate=24000, duration_s=0.1)


def _make_server(**cfg_kw) -> DialogueServer:  # noqa: ANN003
    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    return DialogueServer(
        ServerConfig(**cfg_kw),
        llm_client=FakeLLM(),
        tts_client=FakeTTS(),
        card=card,
    )


def _add_client(server: DialogueServer, **kw) -> ClientSession:  # noqa: ANN003
    """直接往 _clients 里塞一个会话（绕过 WebSocket 握手）。"""
    sid = kw.pop("session_id", "sess-0001")
    c = ClientSession(ws=FakeWS(), session_id=sid, **kw)
    c.peer_str = "192.168.137.42:51234"
    server._clients[sid] = c
    server._total_connections += 1
    return c


# ----------------------------------------------------------------------
# 1. 空 clients
# ----------------------------------------------------------------------


def test_snapshot_empty_structure() -> None:
    server = _make_server()
    snap = server.snapshot()

    assert set(snap) == {"server", "clients", "counters"}
    assert snap["clients"] == []
    assert snap["counters"]["active"] == 0


def test_snapshot_server_fields() -> None:
    server = _make_server()
    s = server.snapshot()["server"]

    # 必须包含监控页需要的全部字段
    for key in (
        "host", "port", "character_id", "character_name", "uptime_s",
        "max_connections", "access_token_required",
        "audio_in_sample_rate", "audio_in_channels", "audio_out_sample_rate",
        "protocol_version", "filler_available",
    ):
        assert key in s, f"快照缺少 server.{key}"

    assert s["host"] == "127.0.0.1"
    assert s["port"] == 8765
    assert s["max_connections"] == 4
    assert s["audio_in_sample_rate"] == 16000
    assert s["audio_out_sample_rate"] == 24000
    assert s["protocol_version"] == 1
    assert isinstance(s["uptime_s"], (int, float))


def test_snapshot_character_from_card() -> None:
    """角色信息必须来自角色卡，不是配置里的默认值。"""
    server = _make_server(character_id="wrong")
    s = server.snapshot()["server"]
    assert s["character_id"] == "elysia"
    assert s["character_name"]


def test_snapshot_access_token_flag() -> None:
    assert _make_server().snapshot()["server"]["access_token_required"] is False
    assert (
        _make_server(access_token="secret")
        .snapshot()["server"]["access_token_required"]
        is True
    )


# ----------------------------------------------------------------------
# 2. 单 client 字段完整性
# ----------------------------------------------------------------------


def test_snapshot_single_client_fields() -> None:
    server = _make_server()
    _add_client(server, device_id="esp32s3-01")

    snap = server.snapshot()
    assert len(snap["clients"]) == 1
    c = snap["clients"][0]

    for key in (
        "session_id", "device_id", "peer", "hello_done", "connected_s",
        "state", "state_elapsed_ms", "turn_id", "turn_seq", "is_busy",
        "last_heartbeat_s", "heartbeat_ok", "audio",
        "filler_session_open", "dialogue_turns", "turns_done",
        "last_asr_text", "last_reply_text", "timings",
        "last_error", "errors_recent",
    ):
        assert key in c, f"快照缺少 clients[].{key}"

    assert c["session_id"] == "sess-0001"
    assert c["device_id"] == "esp32s3-01"
    assert c["state"] == "idle"


def test_snapshot_timings_shape() -> None:
    server = _make_server()
    _add_client(server)
    t = server.snapshot()["clients"][0]["timings"]
    for key in ("asr_ms", "llm_first_ms", "llm_total_ms", "tts_first_ms", "playback_ms"):
        assert key in t, f"timings 缺少 {key}"
        assert t[key] is None, "新连接的各阶段耗时应为 None"


def test_snapshot_audio_shape() -> None:
    server = _make_server()
    _add_client(server)
    a = server.snapshot()["clients"][0]["audio"]
    for key in (
        "expect_bytes", "expect_frames", "sent_bytes", "sent_frames",
        "progress", "streaming",
    ):
        assert key in a


# ----------------------------------------------------------------------
# 3. 多 client 互不干扰
# ----------------------------------------------------------------------


def test_snapshot_multiple_clients() -> None:
    server = _make_server()
    _add_client(server, session_id="a", device_id="board-1")
    _add_client(server, session_id="b", device_id="board-2")

    snap = server.snapshot()
    assert len(snap["clients"]) == 2
    ids = {c["session_id"] for c in snap["clients"]}
    assert ids == {"a", "b"}
    assert snap["counters"]["active"] == 2


def test_snapshot_isolated_state() -> None:
    """一个 client 处于 PLAYING，另一个 IDLE，互不影响。"""
    server = _make_server()
    a = _add_client(server, session_id="a")
    b = _add_client(server, session_id="b")

    a.state.to(State.RECORDING)
    a.state.to(State.ASR_PROCESSING)
    a.state.to(State.LLM_PROCESSING)
    a.state.to(State.TTS_PROCESSING)
    a.state.to(State.PLAYING)

    snap = server.snapshot()
    by_id = {c["session_id"]: c for c in snap["clients"]}
    assert by_id["a"]["state"] == "playing"
    assert by_id["b"]["state"] == "idle"


# ----------------------------------------------------------------------
# 4. 心跳换算
# ----------------------------------------------------------------------


def test_snapshot_heartbeat_fresh() -> None:
    server = _make_server()
    _add_client(server)
    c = server.snapshot()["clients"][0]
    assert c["last_heartbeat_s"] < 1.0
    assert c["heartbeat_ok"] is True


def test_snapshot_heartbeat_stale() -> None:
    """心跳超过 HEARTBEAT_TIMEOUT_S 必须标记为不健康。"""
    import time

    server = _make_server()
    client = _add_client(server)
    client.last_heartbeat = time.perf_counter() - (HEARTBEAT_TIMEOUT_S + 5)

    c = server.snapshot()["clients"][0]
    assert c["last_heartbeat_s"] >= HEARTBEAT_TIMEOUT_S
    assert c["heartbeat_ok"] is False


def test_snapshot_connected_duration() -> None:
    import time

    server = _make_server()
    client = _add_client(server)
    client.connected_at = time.perf_counter() - 100

    c = server.snapshot()["clients"][0]
    assert 99 <= c["connected_s"] <= 102


# ----------------------------------------------------------------------
# 5. 音频进度
# ----------------------------------------------------------------------

def test_snapshot_audio_progress() -> None:
    server = _make_server()
    client = _add_client(server)
    client.expect_audio_frames = 100
    client.expect_audio_bytes = 400000
    client.sent_frames = 73
    client.sent_bytes = 292000

    a = server.snapshot()["clients"][0]["audio"]
    assert a["progress"] == pytest.approx(0.73)
    assert a["streaming"] is False


def test_snapshot_audio_streaming_no_progress() -> None:
    """流式下 expect_frames=0 是合法的，progress 必须为 None 而不是 0。"""
    server = _make_server()
    client = _add_client(server)
    client.expect_audio_frames = 0
    client.sent_frames = 42

    a = server.snapshot()["clients"][0]["audio"]
    assert a["progress"] is None, "流式下不应给出百分比（总长未知）"
    assert a["streaming"] is True
    assert a["sent_frames"] == 42


def test_snapshot_audio_progress_capped() -> None:
    """进度不能超过 1.0（防止多算帧）。"""
    server = _make_server()
    client = _add_client(server)
    client.expect_audio_frames = 10
    client.sent_frames = 15

    a = server.snapshot()["clients"][0]["audio"]
    assert a["progress"] == 1.0


# ----------------------------------------------------------------------
# 6. 状态机透传
# ----------------------------------------------------------------------


def test_snapshot_state_enum_values() -> None:
    """8 个状态都应能正确透传为字符串。"""
    server = _make_server()
    client = _add_client(server)

    # IDLE -> RECORDING -> ASR -> LLM -> TTS -> PLAYING -> IDLE
    for target in (
        State.RECORDING, State.ASR_PROCESSING, State.LLM_PROCESSING,
        State.TTS_PROCESSING, State.PLAYING, State.IDLE,
    ):
        client.state.to(target)
        got = server.snapshot()["clients"][0]["state"]
        assert got == target.value


def test_snapshot_filler_state() -> None:
    server = _make_server()
    client = _add_client(server)
    client.state.to(State.RECORDING)
    client.state.to(State.FILLER)
    assert server.snapshot()["clients"][0]["state"] == "filler"


def test_snapshot_busy_flag() -> None:
    server = _make_server()
    client = _add_client(server)
    assert server.snapshot()["clients"][0]["is_busy"] is False

    client.state.to(State.RECORDING)
    assert server.snapshot()["clients"][0]["is_busy"] is True


def test_snapshot_last_error() -> None:
    server = _make_server()
    client = _add_client(server)
    client.state.fail("模拟错误")
    c = server.snapshot()["clients"][0]
    assert c["state"] == "error"
    assert c["last_error"] == "模拟错误"


# ----------------------------------------------------------------------
# 7. 单个 client 出错不影响其它
# ----------------------------------------------------------------------


def test_snapshot_broken_client_isolated() -> None:
    """一个 client 的快照失败，其它仍要正常返回。"""
    server = _make_server()
    good = _add_client(server, session_id="good")
    bad = _add_client(server, session_id="bad")

    # 把 bad 的 state 换成会抛异常的对象
    class Boom:
        @property
        def state(self):
            raise RuntimeError("故意炸")

    bad.state = Boom()  # type: ignore[assignment]

    snap = server.snapshot()
    assert len(snap["clients"]) == 2

    by_id = {c["session_id"]: c for c in snap["clients"]}
    assert "error" in by_id["bad"], "出错的 client 应有 error 字段"
    assert "state" in by_id["good"], "正常 client 不受影响"
    assert by_id["good"]["session_id"] == "good"


# ----------------------------------------------------------------------
# 8. 计数器
# ----------------------------------------------------------------------


def test_snapshot_counters_initial() -> None:
    server = _make_server()
    c = server.snapshot()["counters"]
    assert c == {
        "active": 0,
        "total_connections": 0,
        "rejected_connections": 0,
        "total_turns": 0,
        "total_errors": 0,
    }


def test_snapshot_counters_connections() -> None:
    server = _make_server()
    _add_client(server, session_id="a")
    _add_client(server, session_id="b")
    c = server.snapshot()["counters"]
    assert c["active"] == 2
    assert c["total_connections"] == 2


def test_snapshot_counters_turns() -> None:
    server = _make_server()
    server._total_turns = 5
    assert server.snapshot()["counters"]["total_turns"] == 5


def test_snapshot_counters_errors() -> None:
    server = _make_server()
    server._total_errors = 3
    assert server.snapshot()["counters"]["total_errors"] == 3


# ----------------------------------------------------------------------
# 9. 快照只读
# ----------------------------------------------------------------------


def test_snapshot_is_read_only() -> None:
    """调用 snapshot() 不能改变任何状态。"""
    server = _make_server()
    client = _add_client(server, device_id="x")
    client.state.to(State.RECORDING)
    client.sent_frames = 10
    client.expect_audio_frames = 20

    before_state = client.state.state
    before_frames = client.sent_frames
    before_errors = list(client.recent_errors)
    before_total = server._total_connections

    server.snapshot()
    server.snapshot()

    assert client.state.state == before_state
    assert client.sent_frames == before_frames
    assert client.recent_errors == before_errors
    assert server._total_connections == before_total


def test_snapshot_returns_independent_copy() -> None:
    """修改返回的快照不能影响内部状态。"""
    server = _make_server()
    client = _add_client(server)
    client.recent_errors = [{"code": "x", "message": "y", "at": "t"}]

    snap = server.snapshot()
    snap["clients"][0]["errors_recent"].append({"code": "injected"})

    assert len(client.recent_errors) == 1, "快照应是副本，不能共享引用"


# ----------------------------------------------------------------------
# 10. ClientSession 辅助方法
# ----------------------------------------------------------------------


def test_record_error_ring_buffer() -> None:
    server = _make_server()
    client = _add_client(server)
    for i in range(15):
        client.record_error(f"code{i}", f"msg{i}")

    assert len(client.recent_errors) <= 10, "错误环形缓冲应限制条数"
    assert client.recent_errors[-1]["code"] == "code14"


def test_new_turn_resets_turn_scoped_fields() -> None:
    server = _make_server()
    client = _add_client(server)
    client.sent_frames = 50
    client.timings["asr_ms"] = 120.0
    client.last_asr_text = "旧文本"
    client.recent_errors = [{"code": "old"}]
    client.turns_done = 3

    client.new_turn()

    assert client.sent_frames == 0
    assert client.timings["asr_ms"] is None
    assert client.last_asr_text == ""
    # 跨轮保留的字段
    assert client.recent_errors == [{"code": "old"}]
    assert client.turns_done == 3


# ----------------------------------------------------------------------
# 11. HTTP 端点
# ----------------------------------------------------------------------


def test_http_snapshot_endpoint() -> None:
    server = _make_server()
    _add_client(server, device_id="board-1")

    status, headers, body = asyncio.run(
        server.process_request("/api/devices", {})
    )
    assert status == 200
    data = json.loads(body)
    assert data["counters"]["active"] == 1
    assert len(data["clients"]) == 1

    hmap = {k.lower(): v for k, v in headers}
    assert "application/json" in hmap["content-type"]
    assert hmap.get("cache-control") == "no-store"


def test_http_snapshot_cors() -> None:
    """必须允许跨域 —— 前端在别的端口。"""
    server = _make_server()
    _, headers, _ = asyncio.run(server.process_request("/api/devices", {}))
    hmap = {k.lower(): v for k, v in headers}
    assert hmap.get("access-control-allow-origin") == "*"


def test_http_health_endpoint() -> None:
    server = _make_server()
    _add_client(server)
    status, _, body = asyncio.run(
        server.process_request("/api/devices/health", {})
    )
    assert status == 200
    d = json.loads(body)
    assert d["ok"] is True
    assert d["active"] == 1


def test_http_404_unknown_path() -> None:
    server = _make_server()
    status, _, body = asyncio.run(
        server.process_request("/api/devices/nope", {})
    )
    assert status == 404
    d = json.loads(body)
    assert d["error"] == "not_found"
    assert "/api/devices" in d["available"]


def test_http_method_not_distinguishable() -> None:
    """websockets 12 的 process_request 拿不到 HTTP 方法。

    这是实测确认的约束（见 scripts/_diag_process_request.py）：
    ``process_request(path, request_headers)`` 的 ``request_headers``
    只含 HTTP 头，不含方法。因此无法在钩子里区分 GET/POST。

    影响：POST 到只读端点也会返回快照。这是可接受的 ——
    端点本身不提供任何写操作，POST 也无副作用。
    但不能因此声称"只支持 GET"（那是没验证过的说法）。
    """
    server = _make_server()
    # 即使传了看似带方法的信息，也按路径响应
    status, _, body = asyncio.run(
        server.process_request("/api/devices", {"Method": "POST"})
    )
    assert status == 200, "方法不可知，按路径响应"
    assert "clients" in json.loads(body)


def test_http_query_string_ignored() -> None:
    server = _make_server()
    status, _, _ = asyncio.run(
        server.process_request("/api/devices?foo=bar", {})
    )
    assert status == 200


def test_http_trailing_slash() -> None:
    server = _make_server()
    status, _, _ = asyncio.run(server.process_request("/api/devices/", {}))
    assert status == 200


def test_http_websocket_path_passes_through() -> None:
    """非 /api/devices 路径必须返回 None，交给 WebSocket 握手。"""
    server = _make_server()
    for path in ("/", "/ws", "/anything"):
        assert asyncio.run(server.process_request(path, {})) is None


def test_http_sse_returns_501_not_fake_support() -> None:
    """未实现 SSE 就必须明确告知，不能假装支持。"""
    server = _make_server()
    status, _, body = asyncio.run(
        server.process_request("/api/devices/stream", {})
    )
    assert status == 501
    d = json.loads(body)
    assert d["error"] == "sse_not_supported"
    assert "轮询" in d["message"]
    assert d["poll_interval_ms"] == 1000
