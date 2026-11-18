"""端到端验收：设备监控 Web 端 + zjy13 快照端点。

启动真实的 zjy13 DialogueServer（替身 LLM/TTS），模拟 3 块板子在不同状态，
然后验证前端需要的一切数据都能正确拿到。

重点验证「数据准确性」—— 监控页最大的风险是数字看着对但实际错。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/d/xiangmudaima/zjy13")

CHAR_DIR = Path("/mnt/d/xiangmudaima/zjy13/server/characters")
PORT = 18766

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    print(f"  [PASS] {msg}")
    PASS += 1


def bad(msg: str) -> None:
    global FAIL
    print(f"  [FAIL] {msg}")
    FAIL += 1


def check(cond: bool, msg: str) -> None:
    ok(msg) if cond else bad(msg)


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
    pass


class FakeTTS:
    def __init__(self) -> None:
        from server.tts import TTSConfig

        self.config = TTSConfig()
        self.last_stream_sample_rate = 24000


async def http_get(port: int, path: str, timeout: float = 5.0) -> tuple[int, dict, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(-1), timeout=timeout)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass

    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split()[1]) if lines and len(lines[0].split()) >= 2 else 0
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status, headers, body


async def main() -> int:
    from server.dialogue.character import CharacterCard
    from server.ws.server import ClientSession, DialogueServer, ServerConfig
    from server.ws.states import State

    print("=" * 84)
    print(" 设备监控端到端验收")
    print("=" * 84)

    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    server = DialogueServer(
        ServerConfig(host="127.0.0.1", port=PORT),
        llm_client=FakeLLM(),
        tts_client=FakeTTS(),
        card=card,
    )

    # ---- 造 3 块板子，处于不同状态 ----
    # A: 播放中（有进度、有耗时）
    a = ClientSession(ws=FakeWS(), session_id="board-a", device_id="esp32s3-01")
    a.peer_str = "192.168.137.41:50001"
    a.hello_done = True
    for st in (State.RECORDING, State.ASR_PROCESSING, State.LLM_PROCESSING,
               State.TTS_PROCESSING, State.PLAYING):
        a.state.to(st)
    a.expect_audio_frames = 134
    a.expect_audio_bytes = 547200
    a.sent_frames = 98
    a.sent_bytes = 400128
    a.timings = {
        "asr_ms": 120.5, "llm_first_ms": 646.0, "llm_total_ms": 996.0,
        "tts_first_ms": 1685.0, "playback_ms": None,
    }
    a.last_asr_text = "今天天气怎么样"
    a.last_reply_text = "今天晴，适合出门走走"
    a.turns_done = 3
    a.record_error("tts_failed", "模拟的一次失败")

    # B: 空闲（心跳超时）
    b = ClientSession(ws=FakeWS(), session_id="board-b", device_id="esp32s3-02")
    b.peer_str = "192.168.137.42:50002"
    b.hello_done = True
    import time
    b.last_heartbeat = time.perf_counter() - 45  # 超过 30s 阈值

    # C: 录音中（流式模式：expect_frames=0）
    c = ClientSession(ws=FakeWS(), session_id="board-c", device_id="esp32s3-03")
    c.peer_str = "192.168.137.43:50003"
    c.hello_done = True
    c.state.to(State.RECORDING)
    c.expect_audio_frames = 0
    c.sent_frames = 17

    for client in (a, b, c):
        server._clients[client.session_id] = client
        server._total_connections += 1

    server._total_turns = 12
    server._total_errors = 4

    print(f"\n[准备] 3 块板子：播放中 / 空闲+心跳超时 / 录音中(流式)")

    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.8)

    # ---- 1. 快照可达 ----
    print("\n[1] 快照可达性")
    try:
        status, headers, body = await http_get(PORT, "/api/devices")
        check(status == 200, f"状态码 200（实际 {status}）")
        snap = json.loads(body)
        check(isinstance(snap, dict), "返回合法 JSON 对象")
    except Exception as e:  # noqa: BLE001
        bad(f"快照请求失败: {e}")
        task.cancel()
        return 1

    # ---- 2. 服务信息 ----
    print("\n[2] 服务信息")
    s = snap["server"]
    check(s["character_id"] == "elysia", f"角色 ID（{s['character_id']}）")
    check(s["max_connections"] == 4, f"最大连接（{s['max_connections']}）")
    check(s["audio_in_sample_rate"] == 16000, "上行采样率 16000")
    check(s["audio_out_sample_rate"] == 24000, "下行采样率 24000")
    check(s["protocol_version"] == 1, "协议版本 1")
    check(s["uptime_s"] > 0, f"运行时长 {s['uptime_s']}s")
    check(isinstance(s["filler_available"], bool), "填充音可用性可读")

    # ---- 3. 三块板子都在 ----
    print("\n[3] 设备列表")
    clients = snap["clients"]
    check(len(clients) == 3, f"返回 3 块板子（实际 {len(clients)}）")
    by_id = {x["session_id"]: x for x in clients}
    check(set(by_id) == {"board-a", "board-b", "board-c"}, "会话 ID 正确")
    check(by_id["board-a"]["device_id"] == "esp32s3-01", "device_id 正确")

    # ---- 4. 状态透传 ----
    print("\n[4] 状态透传（8 种枚举）")
    check(by_id["board-a"]["state"] == "playing", f"A 播放中（{by_id['board-a']['state']}）")
    check(by_id["board-b"]["state"] == "idle", f"B 空闲（{by_id['board-b']['state']}）")
    check(by_id["board-c"]["state"] == "recording", f"C 录音中（{by_id['board-c']['state']}）")

    # ---- 5. 心跳判定 ----
    print("\n[5] 心跳判定（阈值 30s）")
    check(by_id["board-a"]["heartbeat_ok"] is True, "A 心跳正常")
    check(by_id["board-b"]["heartbeat_ok"] is False,
          f"B 心跳超时（{by_id['board-b']['last_heartbeat_s']}s）")
    check(by_id["board-b"]["last_heartbeat_s"] >= 30, "B 心跳秒数 >= 30")

    # ---- 6. 音频进度 ----
    print("\n[6] 音频进度")
    a_audio = by_id["board-a"]["audio"]
    check(a_audio["expect_frames"] == 134, f"帧数 134（{a_audio['expect_frames']}）")
    check(a_audio["sent_frames"] == 98, f"已发 98（{a_audio['sent_frames']}）")
    check(abs(a_audio["progress"] - 0.731) < 0.01,
          f"进度 73.1%（{a_audio['progress']}）")
    check(a_audio["streaming"] is False, "A 非流式")

    c_audio = by_id["board-c"]["audio"]
    check(c_audio["streaming"] is True, "C 是流式（expect_frames=0）")
    check(c_audio["progress"] is None, "C 流式下不给百分比")
    check(c_audio["sent_frames"] == 17, f"C 已发 17 帧（{c_audio['sent_frames']}）")

    # ---- 7. 阶段耗时 ----
    print("\n[7] 阶段耗时")
    t = by_id["board-a"]["timings"]
    check(t["asr_ms"] == 120.5, f"ASR 120.5ms（{t['asr_ms']}）")
    check(t["llm_first_ms"] == 646.0, f"LLM 首字 646ms（{t['llm_first_ms']}）")
    check(t["tts_first_ms"] == 1685.0, f"TTS 首块 1685ms（{t['tts_first_ms']}）")
    check(t["playback_ms"] is None, "播放未完成时为 null")

    # ---- 8. 文本与错误 ----
    print("\n[8] 文本与错误")
    check(by_id["board-a"]["last_asr_text"] == "今天天气怎么样", "ASR 文本正确")
    check(by_id["board-a"]["last_reply_text"] == "今天晴，适合出门走走", "回复文本正确")
    errs = by_id["board-a"]["errors_recent"]
    check(len(errs) == 1, f"错误 1 条（{len(errs)}）")
    check(errs[0]["code"] == "tts_failed", f"错误码（{errs[0]['code']}）")
    check(by_id["board-b"]["errors_recent"] == [], "B 无错误")

    # ---- 9. 计数器 ----
    print("\n[9] 计数器")
    ct = snap["counters"]
    check(ct["active"] == 3, f"活动 3（{ct['active']}）")
    check(ct["total_connections"] == 3, f"累计连接 3（{ct['total_connections']}）")
    check(ct["total_turns"] == 12, f"累计轮次 12（{ct['total_turns']}）")
    check(ct["total_errors"] == 4, f"累计错误 4（{ct['total_errors']}）")

    # ---- 10. 只读性 ----
    print("\n[10] 快照只读性")
    before_state = by_id["board-a"]["state"]
    before_frames = by_id["board-a"]["audio"]["sent_frames"]
    await http_get(PORT, "/api/devices")
    await http_get(PORT, "/api/devices")
    _, _, body3 = await http_get(PORT, "/api/devices")
    snap3 = json.loads(body3)
    a3 = {x["session_id"]: x for x in snap3["clients"]}["board-a"]
    check(a3["state"] == before_state, "多次快照状态不变")
    check(a3["audio"]["sent_frames"] == before_frames, "多次快照帧数不变")

    # ---- 11. 前端契约字段完整性 ----
    print("\n[11] 前端契约字段完整性")
    required_client = {
        "session_id", "device_id", "peer", "hello_done", "connected_s",
        "state", "state_elapsed_ms", "turn_id", "turn_seq", "is_busy",
        "last_heartbeat_s", "heartbeat_ok", "audio", "filler_session_open",
        "dialogue_turns", "turns_done", "last_asr_text", "last_reply_text",
        "timings", "last_error", "errors_recent",
    }
    missing = required_client - set(by_id["board-a"].keys())
    check(not missing, f"clients[] 字段完整（缺 {missing or '无'}）")

    required_audio = {
        "expect_bytes", "expect_frames", "sent_bytes", "sent_frames",
        "progress", "streaming",
    }
    missing_a = required_audio - set(a_audio.keys())
    check(not missing_a, f"audio 字段完整（缺 {missing_a or '无'}）")

    required_timings = {
        "asr_ms", "llm_first_ms", "llm_total_ms", "tts_first_ms", "playback_ms",
    }
    missing_t = required_timings - set(t.keys())
    check(not missing_t, f"timings 字段完整（缺 {missing_t or '无'}）")

    # ---- 12. CORS（前端跨端口） ----
    print("\n[12] CORS 与缓存")
    check(headers.get("access-control-allow-origin") == "*",
          f"CORS 允许任意来源（{headers.get('access-control-allow-origin')}）")
    check(headers.get("cache-control") == "no-store", "禁缓存（避免拿到旧状态）")

    # ---- 13. 设备断开后消失 ----
    print("\n[13] 设备断开后从快照消失")
    server._clients.pop("board-c", None)
    _, _, body4 = await http_get(PORT, "/api/devices")
    snap4 = json.loads(body4)
    check(len(snap4["clients"]) == 2, f"剩 2 块（{len(snap4['clients'])}）")
    check(snap4["counters"]["active"] == 2, "活动计数同步为 2")

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass

    print()
    print("=" * 84)
    print(f" 结果: {PASS} 通过, {FAIL} 失败")
    print("=" * 84)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
