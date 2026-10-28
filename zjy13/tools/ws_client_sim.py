"""客户端模拟器：模拟 ESP32 开发板的行为。

用途（V2 第十四节阶段四明确要求）：
- 在没有真实硬件的情况下验证协议是否够用
- 测试音频上传、文字返回、音频下载
- 测试重复消息、超时、取消、连接中断

用法：
    # 发一段 WAV，跑完整一轮
    python -m tools.ws_client_sim --wav tests/audio/asr_example.wav

    # 只测握手与心跳
    python -m tools.ws_client_sim --hello-only

    # 中途取消
    python -m tools.ws_client_sim --wav x.wav --cancel-after 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

import websockets

from server.protocol import (
    ASR_FINAL,
    BINARY_MAGIC,
    CANCEL,
    DEVICE_HELLO,
    ERROR,
    HEARTBEAT,
    HEARTBEAT_ACK,
    LLM_DELTA,
    LLM_FINAL,
    LLM_START,
    MAX_AUDIO_CHUNK_BYTES,
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
)

logger = logging.getLogger("ws.sim")


@dataclass(slots=True)
class TurnTrace:
    """一轮的事件轨迹与计时。"""

    events: list[tuple[str, float]] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    audio_bytes: int = 0
    audio_frames: int = 0
    final_text: str = ""
    errors: list[dict] = field(default_factory=list)
    started: float = 0.0

    def mark(self, name: str, t0: float) -> None:
        self.events.append((name, (time.perf_counter() - t0) * 1000))

    def to_dict(self) -> dict:
        return {
            "events": [(n, round(ms, 1)) for n, ms in self.events],
            "texts": self.texts,
            "final_text": self.final_text,
            "audio_bytes": self.audio_bytes,
            "audio_frames": self.audio_frames,
            "errors": self.errors,
        }


class DeviceSimulator:
    """模拟开发板。"""

    def __init__(
        self,
        url: str,
        *,
        token: str = "",
        device_id: str = "sim-esp32",
        out_channels: int = 4,
        out_sample_rate: int = 16000,
    ) -> None:
        self.url = url
        self.token = token
        self.device_id = device_id
        self.out_channels = out_channels
        self.out_sample_rate = out_sample_rate
        self.ws: Any = None
        self.audio_format: dict = {}
        self.trace = TurnTrace()
        self._turn_seq = 0
        self._recording_frames: list[bytes] = []
        self._down_chunks: list[bytes] = []
        self._expect_bytes = 0
        self._expect_frames = 0
        self._playback_reported = False

    # ------------------------------------------------------------------

    async def connect(self) -> dict:
        """建立连接并完成握手。返回 server_hello 的音频格式。"""
        self.ws = await websockets.connect(self.url, max_size=1 << 20)
        await self.ws.send(json.dumps(
            Envelope(
                type=DEVICE_HELLO,
                payload={
                    "device_id": self.device_id,
                    "firmware": "sim-1.0",
                    "token": self.token,
                    "capabilities": ["mic", "speaker", "lcd"],
                },
            ).to_dict(),
            ensure_ascii=False,
        ))
        raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
        msg = json.loads(raw)
        if msg.get("type") == ERROR:
            raise RuntimeError(f"握手被拒: {msg.get('code')}: {msg.get('message')}")
        if msg.get("type") != SERVER_HELLO:
            raise RuntimeError(f"握手响应异常: {msg.get('type')}")
        self.audio_format = msg
        logger.info(
            "握手成功: 上行 %s / 下行 %s",
            msg.get("audio_in"), msg.get("audio_out"),
        )
        return msg

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    # ------------------------------------------------------------------

    async def send_heartbeat(self) -> dict:
        await self.ws.send(json.dumps(
            Envelope(type=HEARTBEAT).to_dict(), ensure_ascii=False))
        raw = await asyncio.wait_for(self.ws.recv(), timeout=5)
        return json.loads(raw)

    async def start_recording(self, turn_id: str = "") -> dict:
        self._recording_frames = []
        self._down_chunks = []
        self.trace = TurnTrace()
        # 每轮必须重置，否则第二轮不会上报 playback_done（实测踩过）
        self._expect_bytes = 0
        self._expect_frames = 0
        self._playback_reported = False
        msg = await self._send_and_wait(
            Envelope(
                type=RECORDING_START,
                payload={
                    "sample_rate": self.out_sample_rate,
                    "channels": self.out_channels,
                    "bits": 16,
                    "encoding": "pcm_s16le",
                    "channel_layout": "RMNM",
                },
            ).to_dict(),
            expect={SESSION_START, ERROR},
        )
        return msg

    async def send_audio_wav(self, wav_path: Path, *, chunk_ms: int = 100) -> int:
        """把 WAV 转成开发板格式（多通道交错）并分块发送。

        模拟真实开发板：ES7210 输出 4 通道，通道 0 是回声参考。
        """
        import numpy as np

        with wave.open(str(wav_path), "rb") as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            raw = w.readframes(w.getnframes())

        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        if ch > 1:
            data = data.reshape(-1, ch).mean(axis=1)

        # 重采样到目标采样率
        if sr != self.out_sample_rate and data.size:
            n_out = int(round(data.size / sr * self.out_sample_rate))
            x_old = np.arange(data.size, dtype=np.float64)
            x_new = np.linspace(0, data.size - 1, n_out, dtype=np.float64)
            data = np.interp(x_new, x_old, data).astype(np.float32)

        # 构造成 4 通道：channel0=回声参考（模拟扬声器回采），1/3=双麦
        ref = (data * 0.3).astype(np.float32)
        mic1 = data
        zero = np.zeros_like(data)
        mic2 = data
        inter = np.stack([ref, mic1, zero, mic2], axis=1).reshape(-1)
        pcm = np.round(np.clip(inter, -1, 1) * 32767).astype("<i2").tobytes()

        frames_per_chunk = int(self.out_sample_rate * chunk_ms / 1000)
        bytes_per_chunk = frames_per_chunk * self.out_channels * 2
        if bytes_per_chunk > MAX_AUDIO_CHUNK_BYTES:
            raise ValueError(
                f"分块过大: {bytes_per_chunk} > {MAX_AUDIO_CHUNK_BYTES}。"
                f"请减小 --chunk-ms（当前 {chunk_ms}ms，"
                f"{self.out_sample_rate}Hz/{self.out_channels}ch）"
            )

        seq = 0
        for off in range(0, len(pcm), bytes_per_chunk):
            payload = pcm[off:off + bytes_per_chunk]
            frame = AudioFrame(
                msg_type=BinaryType.AUDIO_UP,
                seq=seq,
                turn_id_short=0,
                payload=payload,
            )
            await self.ws.send(frame.encode())
            seq += 1
            await asyncio.sleep(0.005)  # 模拟实时采集节奏
        return seq

    async def stop_recording(self, *, timeout: float = 120) -> TurnTrace:
        """发送结束标记并等待整轮完成（含音频接收）。

        循环直到收到 turn_done / turn_cancelled / fatal error。
        注意：中间会穿插 heartbeat_ack 等消息，不能提前退出。
        """
        t0 = time.perf_counter()
        self.trace.started = t0
        await self.ws.send(json.dumps(
            Envelope(type=RECORDING_STOP).to_dict(), ensure_ascii=False))

        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            if isinstance(raw, (bytes, bytearray)):
                self._on_binary(bytes(raw), t0)
                continue
            msg = json.loads(raw)
            done = await self._on_json(msg, t0)
            if done:
                break
        # 给服务端一点时间完成状态回写，避免下一轮撞 TURN_BUSY
        await asyncio.sleep(0.05)
        return self.trace

    async def cancel(self) -> dict:
        await self.ws.send(json.dumps(
            Envelope(type=CANCEL).to_dict(), ensure_ascii=False))
        raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
        return json.loads(raw)

    async def send_raw(self, payload: str) -> dict | None:
        """发送任意原始字符串（用于测试坏消息）。"""
        await self.ws.send(payload)
        try:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=5)
        except asyncio.TimeoutError:
            return None
        return json.loads(raw) if not isinstance(raw, (bytes, bytearray)) else None

    # ------------------------------------------------------------------

    async def _send_and_wait(self, msg: dict, *, expect: set[str]) -> dict:
        await self.ws.send(json.dumps(msg, ensure_ascii=False))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
            if isinstance(raw, (bytes, bytearray)):
                continue
            got = json.loads(raw)
            if got.get("type") in expect:
                return got

    def _on_binary(self, data: bytes, t0: float) -> None:
        if data[0] != BINARY_MAGIC:
            logger.warning("收到非法帧头 0x%02X", data[0])
            return
        frame = AudioFrame.decode(data)
        if frame.msg_type != BinaryType.AUDIO_DOWN:
            return
        if self.trace.audio_frames == 0:
            self.trace.mark("first_audio", t0)
        self.trace.audio_frames += 1
        self.trace.audio_bytes += len(frame.payload)

        # 收齐预期帧数后回报 playback_done（模拟播放完成）
        if (self._expect_frames
                and self.trace.audio_frames >= self._expect_frames
                and not self._playback_reported):
            self._playback_reported = True
            self.trace.mark("audio_complete", t0)
            asyncio.create_task(self._report_playback_done())

    async def _report_playback_done(self) -> None:
        """模拟播放耗时后上报（真实设备是播完才报）。"""
        await asyncio.sleep(0.05)
        try:
            await self.ws.send(json.dumps(
                Envelope(
                    type=PLAYBACK_DONE,
                    payload={
                        "received_bytes": self.trace.audio_bytes,
                        "received_frames": self.trace.audio_frames,
                    },
                ).to_dict(),
                ensure_ascii=False,
            ))
        except Exception:  # noqa: BLE001
            pass

    async def _on_json(self, msg: dict, t0: float) -> bool:
        mtype = msg.get("type")
        if mtype == ASR_FINAL:
            self.trace.mark("asr_final", t0)
            self.trace.texts.append(msg.get("text", ""))
            print(f"  [ASR] {msg.get('text')}")
        elif mtype == LLM_START:
            self.trace.mark("llm_start", t0)
        elif mtype == LLM_DELTA:
            if not any(n == "llm_first_delta" for n, _ in self.trace.events):
                self.trace.mark("llm_first_delta", t0)
        elif mtype == LLM_FINAL:
            self.trace.mark("llm_final", t0)
            self.trace.final_text = msg.get("text", "")
            print(f"  [LLM] {self.trace.final_text}")
        elif mtype == TTS_START:
            self.trace.mark("tts_start", t0)
        elif mtype == PLAYBACK_START:
            self.trace.mark("playback_start", t0)
            self._expect_bytes = int(msg.get("bytes", 0) or 0)
            self._expect_frames = int(msg.get("frames", 0) or 0)
            print(
                f"  [TTS] {msg.get('duration_s')}s 音频, "
                f"采样率 {msg.get('sample_rate')}, "
                f"{self._expect_bytes} 字节 / {self._expect_frames} 帧"
            )
            # 不在这里发 playback_done：必须等收齐音频帧后再发，
            # 否则服务端会在音频发完前就收尾，导致丢帧。
        elif mtype == TURN_DONE:
            self.trace.mark("turn_done", t0)
            print(f"  [完成] reason={msg.get('reason')}")
            return True
        elif mtype == TURN_CANCELLED:
            self.trace.mark("cancelled", t0)
            print(f"  [已取消] {msg.get('reason')}")
            return True
        elif mtype == ERROR:
            self.trace.errors.append(msg)
            print(f"  [错误] {msg.get('code')}: {msg.get('message')}")
            if msg.get("fatal"):
                return True
        elif mtype == HEARTBEAT_ACK:
            self.trace.mark("heartbeat_ack", t0)
        return False


async def run_scenario(args: argparse.Namespace) -> int:
    sim = DeviceSimulator(
        args.url,
        token=args.token,
        out_channels=args.in_channels,
        out_sample_rate=args.in_sample_rate,
    )
    try:
        hello = await sim.connect()
        print(f"  服务端: {hello.get('server_name')}")
        print(f"  上行格式: {hello['audio_in']}")
        print(f"  下行格式: {hello['audio_out']}")
        print()

        if args.hello_only:
            ack = await sim.send_heartbeat()
            print(f"  心跳: {ack.get('type')}")
            return 0

        if args.bad_message:
            print("--- 测试坏消息 ---")
            got = await sim.send_raw('{"type":')
            print(f"  非法 JSON → {got and got.get('code')}")
            got = await sim.send_raw(
                json.dumps({"type": "recording_start", "protocol_version": 999}))
            print(f"  版本不符 → {got and got.get('code')}, fatal={got and got.get('fatal')}")
            return 0

        # ---- 多轮循环 ----
        traces: list[TurnTrace] = []
        for r in range(1, max(1, args.rounds) + 1):
            label = f"第 {r} 轮"
            print(f"--- {label}: {args.wav} ---")
            started = await sim.start_recording()
            turn_id = started.get("turn_id", "")
            print(f"  turn_id={turn_id}")

            n = await sim.send_audio_wav(Path(args.wav), chunk_ms=args.chunk_ms)
            print(f"  已发送 {n} 个音频块")

            if args.cancel_after and r == 1:
                await asyncio.sleep(args.cancel_after)
                cancelled = await sim.cancel()
                print(f"  取消响应: {cancelled.get('type')}")
                return 0

            trace = await sim.stop_recording()
            traces.append(trace)
            print()
            print(f"  === {label} 计时 ===")
            for name, ms in trace.events:
                print(f"    {name:20} {ms:8.0f} ms")
            print(f"    下行音频: {trace.audio_frames} 帧 / {trace.audio_bytes} 字节")
            if trace.audio_bytes:
                dur = trace.audio_bytes / 2 / 24000
                print(f"    音频时长约 {dur:.2f}s")
            if trace.errors:
                print(f"    !! 本轮有 {len(trace.errors)} 个错误")
            print()

        # 汇总
        if len(traces) > 1:
            print("=== 多轮汇总 ===")
            ok = sum(1 for t in traces if not t.errors)
            print(f"  成功 {ok}/{len(traces)} 轮")
            e2e = [ms for t in traces for n, ms in t.events if n == "turn_done"]
            if e2e:
                print(f"  端到端均值 {sum(e2e)/len(e2e):.0f} ms")
                print(f"  端到端最大 {max(e2e):.0f} ms")
            total_audio = sum(t.audio_bytes for t in traces)
            print(f"  累计音频 {total_audio} 字节")

        trace = traces[0] if traces else TurnTrace()
        return 0 if not any(t.errors for t in traces) else 1
    finally:
        await sim.close()


def main() -> int:
    logging.basicConfig(
        level=logging.DEBUG if "--verbose" in sys.argv else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="WebSocket 客户端模拟器（模拟 ESP32）")
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--wav", default=str(root / "tests" / "audio" / "asr_example.wav"))
    ap.add_argument("--token", default="")
    ap.add_argument("--in-channels", type=int, default=4)
    ap.add_argument("--in-sample-rate", type=int, default=16000)
    ap.add_argument("--chunk-ms", type=int, default=100)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--cancel-after", type=float, default=0.0,
                    help="秒；>0 时在发送后取消本轮")
    ap.add_argument("--hello-only", action="store_true")
    ap.add_argument("--bad-message", action="store_true")
    args = ap.parse_args()
    return asyncio.run(run_scenario(args))


if __name__ == "__main__":
    raise SystemExit(main())