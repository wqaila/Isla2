"""启动一个带监控端点的 zjy13 服务（替身 LLM/TTS，无需真模型）。

用途：给前端联调用。真实服务需要 FunASR + TTS，环境不一定齐；
监控端点本身与这些无关，用替身即可完整验证。

用法：
    wsl -e bash -lc "cd /mnt/d/xiangmudaima/zjy13 && \
      source ~/cosyvoice-venv/bin/activate && \
      PYTHONPATH=. python scripts/serve_monitor_demo.py"

然后用浏览器打开 zjy14 前端 → 设备管理页。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/d/xiangmudaima/zjy13")

CHAR_DIR = Path("/mnt/d/xiangmudaima/zjy13/server/characters")


class FakeWS:
    def __init__(self) -> None:
        self.remote_address = ("192.168.137.41", 50001)

    async def send(self, d: object) -> None:
        pass

    async def close(self) -> None:
        pass


class FakeLLM:
    pass


class FakeTTS:
    def __init__(self) -> None:
        from server.tts import TTSConfig

        self.config = TTSConfig()
        self.last_stream_sample_rate = 24000


def _seed_demo_clients(server) -> None:  # noqa: ANN001
    """塞 3 块模拟板子，覆盖不同状态，便于前端看效果。"""
    from server.ws.server import ClientSession
    from server.ws.states import State

    # A：播放中，有完整进度与耗时
    a = ClientSession(ws=FakeWS(), session_id="demo-a", device_id="esp32s3-01")
    a.peer_str = "192.168.137.41:50001"
    a.hello_done = True
    for st in (
        State.RECORDING, State.ASR_PROCESSING, State.LLM_PROCESSING,
        State.TTS_PROCESSING, State.PLAYING,
    ):
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

    # B：空闲，心跳超时（演示异常态）
    b = ClientSession(ws=FakeWS(), session_id="demo-b", device_id="esp32s3-02")
    b.peer_str = "192.168.137.42:50002"
    b.hello_done = True
    b.last_heartbeat = time.perf_counter() - 45
    b.record_error("tts_failed", "演示用的错误记录")

    # C：录音中，流式模式
    c = ClientSession(ws=FakeWS(), session_id="demo-c", device_id="esp32s3-03")
    c.peer_str = "192.168.137.43:50003"
    c.hello_done = True
    c.state.to(State.RECORDING)
    c.expect_audio_frames = 0
    c.sent_frames = 17

    for cl in (a, b, c):
        server._clients[cl.session_id] = cl
        server._total_connections += 1
    server._total_turns = 12
    server._total_errors = 4


async def main() -> int:
    from server.dialogue.character import CharacterCard
    from server.ws.server import DialogueServer, ServerConfig

    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    server = DialogueServer(
        ServerConfig(host="127.0.0.1", port=8765),
        llm_client=FakeLLM(),
        tts_client=FakeTTS(),
        card=card,
    )
    _seed_demo_clients(server)

    print("=" * 72)
    print(" 监控演示服务")
    print("=" * 72)
    print(f"  快照: http://127.0.0.1:8765/api/devices")
    print(f"  健康: http://127.0.0.1:8765/api/devices/health")
    print(f"  已塞入 {len(server._clients)} 块模拟板子（覆盖 3 种状态）")
    print("  前端：打开 zjy14 前端 → 设备管理")
    print("  Ctrl+C 停止")
    print()

    await server.serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
