"""验证 zjy13 的设备监控 HTTP 端点真实可用。

启动真实的 DialogueServer（用替身 LLM/TTS，不加载模型），
然后用 HTTP 请求打 /api/devices，验证：
  1. 端点真的响应（不是只靠单元测试）
  2. WebSocket 握手路径不被破坏
  3. JSON 结构可被前端消费

**关键坑**：不能用 urllib 在同一个事件循环里发请求 ——
urllib 是阻塞的，会卡死 serve() 所在的循环，请求必然超时。
必须用 asyncio.open_connection 手写 HTTP 请求。
（第一版就是踩了这个，表现为「单元测试全过但真实请求超时」。）
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/d/xiangmudaima/zjy13")

CHAR_DIR = Path("/mnt/d/xiangmudaima/zjy13/server/characters")


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
    """用 asyncio 原生连接发 HTTP GET，返回 (状态码, 头字典, body)。

    为什么不用 urllib/aiohttp：
      urllib 是同步阻塞的，在 serve() 的同一个事件循环里调用会死锁。
      aiohttp 不是本项目依赖。手写最省事，且能精确控制。
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            f"Connection: close\r\n\r\n".encode()
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
    status = 0
    if lines:
        parts = lines[0].split()
        if len(parts) >= 2:
            status = int(parts[1])
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

    print("=" * 80)
    print(" 设备监控 HTTP 端点真实验证")
    print("=" * 80)

    PORT = 18765

    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    server = DialogueServer(
        ServerConfig(host="127.0.0.1", port=PORT),
        llm_client=FakeLLM(),
        tts_client=FakeTTS(),
        card=card,
    )

    # 手动塞两个会话（模拟两块板子）
    for i, (sid, dev) in enumerate(
        [("sess-aaa", "esp32s3-01"), ("sess-bbb", "esp32s3-02")]
    ):
        c = ClientSession(ws=FakeWS(), session_id=sid, device_id=dev)
        c.peer_str = f"192.168.137.{40 + i}:51234"
        server._clients[sid] = c
        server._total_connections += 1
        if i == 0:
            for st in (
                State.RECORDING, State.ASR_PROCESSING, State.LLM_PROCESSING,
                State.TTS_PROCESSING, State.PLAYING,
            ):
                c.state.to(st)
            c.expect_audio_frames = 134
            c.expect_audio_bytes = 547200
            c.sent_frames = 98
            c.sent_bytes = 400000
            c.timings["asr_ms"] = 120.0
            c.timings["llm_first_ms"] = 646.0
            c.timings["tts_first_ms"] = 1685.0
            c.last_asr_text = "今天天气怎么样"
            c.last_reply_text = "今天晴，适合出门"

    print(f"\n[1] 准备：{len(server._clients)} 个模拟会话")

    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.8)

    ok = 0
    fail = 0

    def check(cond: bool, msg: str) -> None:
        nonlocal ok, fail
        if cond:
            print(f"  [PASS] {msg}")
            ok += 1
        else:
            print(f"  [FAIL] {msg}")
            fail += 1

    # ---- 2. 快照端点 ----
    print("\n[2] GET /api/devices")
    try:
        status, headers, body = await http_get(PORT, "/api/devices")
        check(status == 200, f"状态码 200（实际 {status}）")
        check("json" in headers.get("content-type", ""),
              f"Content-Type 正确（{headers.get('content-type')}）")
        check(headers.get("access-control-allow-origin") == "*",
              f"CORS 允许跨域（{headers.get('access-control-allow-origin')}）")
        check(headers.get("cache-control") == "no-store", "禁缓存")

        data = json.loads(body)
        check("server" in data and "clients" in data and "counters" in data,
              "顶层结构完整")
        check(data["counters"]["active"] == 2,
              f"活动连接数 2（实际 {data['counters']['active']}）")
        check(data["server"]["character_id"] == "elysia",
              f"角色 ID 来自角色卡（{data['server']['character_id']}）")

        clients = data["clients"]
        check(len(clients) == 2, f"返回 2 个客户端（实际 {len(clients)}）")

        playing = [c for c in clients if c["state"] == "playing"]
        check(len(playing) == 1, "有一块板子处于 playing")
        if playing:
            p = playing[0]
            check(p["audio"]["expect_frames"] == 134, "帧数正确")
            check(abs(p["audio"]["progress"] - 0.7313) < 0.01,
                  f"进度约 73%（实际 {p['audio']['progress']}）")
            check(p["timings"]["tts_first_ms"] == 1685.0, "TTS 耗时正确")
            check(p["last_asr_text"] == "今天天气怎么样", "ASR 文本正确")
            print(f"       设备 {p['device_id']}  peer={p['peer']}  "
                  f"心跳 {p['last_heartbeat_s']}s")
    except Exception as e:  # noqa: BLE001
        check(False, f"快照请求失败: {type(e).__name__}: {e}")

    # ---- 3. health 端点 ----
    print("\n[3] GET /api/devices/health")
    try:
        status, _, body = await http_get(PORT, "/api/devices/health")
        d = json.loads(body)
        check(status == 200, f"状态码 200（实际 {status}）")
        check(d.get("ok") is True, "health ok")
        check(d.get("active") == 2, f"active=2（实际 {d.get('active')}）")
    except Exception as e:  # noqa: BLE001
        check(False, f"health 失败: {e}")

    # ---- 4. 未知路径 404 ----
    print("\n[4] GET /api/devices/nope -> 404")
    try:
        status, _, body = await http_get(PORT, "/api/devices/nope")
        check(status == 404, f"404（实际 {status}）")
        d = json.loads(body)
        check("available" in d, "告知可用路径")
    except Exception as e:  # noqa: BLE001
        check(False, f"404 验证失败: {e}")

    # ---- 5. SSE 明确不支持 ----
    print("\n[5] GET /api/devices/stream -> 501")
    try:
        status, _, body = await http_get(PORT, "/api/devices/stream")
        check(status == 501, f"501（实际 {status}）")
        d = json.loads(body)
        check(d.get("error") == "sse_not_supported", "明确说明未实现")
    except Exception as e:  # noqa: BLE001
        check(False, f"SSE 验证失败: {e}")

    # ---- 6. WebSocket 握手仍可用 ----
    print("\n[6] WebSocket 握手路径未被破坏")
    try:
        import websockets

        async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
            # 协议要求先 device_hello，否则发别的消息会被拒（not_hello）
            await ws.send(json.dumps({
                "type": "device_hello",
                "protocol_version": 1,
                "device_id": "verify-board",
            }))
            msg = await asyncio.wait_for(ws.recv(), timeout=3)
            d = json.loads(msg)
            check(d.get("type") == "server_hello",
                  f"收到 server_hello（实际 {d.get('type')}）")

            # 握手后心跳应正常
            await ws.send(json.dumps({
                "type": "heartbeat", "protocol_version": 1,
            }))
            msg2 = await asyncio.wait_for(ws.recv(), timeout=3)
            d2 = json.loads(msg2)
            check(d2.get("type") == "heartbeat_ack",
                  f"收到 heartbeat_ack（实际 {d2.get('type')}）")
    except Exception as e:  # noqa: BLE001
        check(False, f"WebSocket 握手失败: {type(e).__name__}: {e}")

    # ---- 7. 连接后快照计数更新 ----
    print("\n[7] 连接建立/断开后计数更新")
    try:
        await asyncio.sleep(0.3)  # 等清理完成
        status, _, body = await http_get(PORT, "/api/devices")
        d = json.loads(body)
        check(d["counters"]["active"] == 2,
              f"WebSocket 断开后回到 2（实际 {d['counters']['active']}）")
        check(d["counters"]["total_connections"] == 3,
              f"累计连接 3（实际 {d['counters']['total_connections']}）")
    except Exception as e:  # noqa: BLE001
        check(False, f"计数验证失败: {e}")

    # ---- 8. 连接数超限计数 ----
    print("\n[8] 连接数超限被拒绝时计数")
    try:
        import websockets

        # max_connections=4，已有 2 个模拟会话；再连 3 个，第 3 个应被拒
        conns = []
        for _ in range(3):
            try:
                ws = await websockets.connect(f"ws://127.0.0.1:{PORT}")
                conns.append(ws)
            except Exception:  # noqa: BLE001
                break
        await asyncio.sleep(0.3)
        status, _, body = await http_get(PORT, "/api/devices")
        d = json.loads(body)
        rejected = d["counters"]["rejected_connections"]
        check(rejected >= 1, f"被拒连接已计数（{rejected}）")
        for ws in conns:
            await ws.close()
    except Exception as e:  # noqa: BLE001
        check(False, f"超限验证失败: {e}")

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass

    print()
    print("=" * 80)
    print(f" 结果: {ok} 通过, {fail} 失败")
    print("=" * 80)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
