"""按固件的字节级行为对接服务端，验证协议实现是否正确。

目的：板子还没烧，但**固件的协议层逻辑可以先用 Python 等价复现**。
这样烧录前就能确认"固件发出的字节会被服务端正确理解"。

与 tools/ws_client_sim.py 的区别：
  - 模拟器用的是 Python 的 struct/zlib（服务端同源代码）
  - 本脚本严格按固件 C 代码的**手写小端序列化**和**自实现 CRC32**
    生成字节，用来验证两侧实现是否真的一致
"""

from __future__ import annotations

import asyncio
import json
import struct
import sys
import wave
from pathlib import Path

import numpy as np
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROTOCOL_VERSION = 1
MAGIC = 0xA5
TYPE_UP = 0x01
TYPE_DOWN = 0x02
HEADER = 12


def fw_crc32(s: str) -> int:
    """严格复刻 app_protocol.c 里 app_proto_turn_short 的实现。"""
    crc = 0xFFFFFFFF
    for b in s.encode("utf-8"):
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 & (0xFFFFFFFF if (crc & 1) else 0))
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


def fw_encode_audio(seq: int, turn_short: int, payload: bytes) -> bytes:
    """严格复刻 app_proto_encode_audio：手写 12 字节小端头。"""
    assert len(payload) <= 48 * 1024
    return (
        MAGIC.to_bytes(1, "little")
        + TYPE_UP.to_bytes(1, "little")
        + (seq & 0xFFFF).to_bytes(2, "little")
        + (turn_short & 0xFFFFFFFF).to_bytes(4, "little")
        + len(payload).to_bytes(4, "little")
        + payload
    )


def fw_decode_audio(data: bytes) -> tuple[int, int, int, bytes]:
    """严格复刻 app_proto_decode_audio。"""
    assert len(data) >= HEADER
    magic = data[0]
    assert magic == MAGIC, f"magic 不符: {magic:#x}"
    msg_type = data[1]
    seq = data[2] | (data[3] << 8)
    turn_short = data[4] | (data[5] << 8) | (data[6] << 16) | (data[7] << 24)
    plen = data[8] | (data[9] << 8) | (data[10] << 16) | (data[11] << 24)
    assert len(data) - HEADER >= plen
    return msg_type, seq, turn_short, data[HEADER : HEADER + plen]


def fw_build_hello(device_id: str, firmware: str, token: str) -> str:
    """严格复刻 app_proto_build_hello。"""
    return (
        '{"type":"device_hello","protocol_version":%d,'
        '"device_id":"%s","firmware":"%s","token":"%s"}'
        % (PROTOCOL_VERSION, device_id, firmware, token)
    )


def fw_build_recording_start(sr: int, ch: int, bits: int, enc: str, layout: str) -> str:
    return (
        '{"type":"recording_start","protocol_version":%d,'
        '"sample_rate":%d,"channels":%d,"bits":%d,'
        '"encoding":"%s","channel_layout":"%s"}'
        % (PROTOCOL_VERSION, sr, ch, bits, enc, layout)
    )


def fw_build_recording_stop() -> str:
    return '{"type":"recording_stop","protocol_version":%d}' % PROTOCOL_VERSION


def fw_build_playback_done(turn_id: str, nbytes: int, nframes: int) -> str:
    return (
        '{"type":"playback_done","protocol_version":%d,'
        '"turn_id":"%s","received_bytes":%u,"received_frames":%u}'
        % (PROTOCOL_VERSION, turn_id, nbytes, nframes)
    )


def load_wav_4ch(path: Path) -> np.ndarray:
    """把单声道 WAV 复制成 4 通道交错，模拟 ES7210 的 RMNM 输出。

    固件原样上传 4 通道，服务端负责丢弃 ch0 并降混。
    """
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1, "测试音频应为单声道"
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        sr = w.getframerate()
    # 重采样到 16k（若不同）
    if sr != 16000:
        n = int(len(data) * 16000 / sr)
        data = np.interp(
            np.linspace(0, len(data), n, endpoint=False),
            np.arange(len(data)),
            data,
        ).astype("<i2")
    # ch0=回声参考(0)，ch1=人声，ch2=0，ch3=人声 —— 仿 RMNM
    out = np.zeros((len(data), 4), dtype="<i2")
    out[:, 0] = 0
    out[:, 1] = data
    out[:, 2] = 0
    out[:, 3] = data
    return out


async def run(url: str, wav: Path, rounds: int) -> int:
    print("=" * 64)
    print("  固件协议等价测试")
    print("=" * 64)

    audio = load_wav_4ch(wav)
    sr = 16000
    ch = 4
    chunk_ms = 40
    chunk_samples = sr * chunk_ms // 1000          # 640 采样
    chunk_bytes = chunk_samples * ch * 2            # 5120 字节

    all_ok = True
    async with websockets.connect(url, max_size=None) as ws:
        # ---- 握手（用固件的字节格式）----
        hello = fw_build_hello("esp32s3-fwtest", "fw-equiv-1.0", "")
        await ws.send(hello)
        reply = json.loads(await ws.recv())
        assert reply["type"] == "server_hello", f"期望 server_hello，得到 {reply['type']}"
        print(f"握手 OK  服务端={reply.get('server_name')}")
        print(f"  上行格式 {reply.get('audio_in')}")
        print(f"  下行格式 {reply.get('audio_out')}")

        for r in range(1, rounds + 1):
            print(f"\n--- 第 {r} 轮 ---")

            # ---- 发 recording_start（固件格式）----
            await ws.send(fw_build_recording_start(sr, ch, 16, "pcm_s16le", "RMNM"))

            session = json.loads(await ws.recv())
            assert session["type"] == "session_start", f"期望 session_start，得到 {session['type']}"
            turn_id = session["turn_id"]
            turn_short = fw_crc32(turn_id)
            print(f"  session_start turn_id={turn_id} short={turn_short:08X}")

            # ---- 上传音频块（固件格式：手写小端头）----
            sent_chunks = 0
            sent_bytes = 0
            total_samples = len(audio)
            for off in range(0, total_samples, chunk_samples):
                block = audio[off : off + chunk_samples]
                if len(block) == 0:
                    break
                payload = block.tobytes()
                frame = fw_encode_audio(sent_chunks, turn_short, payload)
                await ws.send(frame)
                sent_chunks += 1
                sent_bytes += len(payload)

            print(f"  已上传 {sent_chunks} 块 / {sent_bytes} 字节")

            # ---- recordding_stop ----
            await ws.send(fw_build_recording_stop())

            # ---- 接收服务端消息直到 turn_done ----
            asr_text = None
            reply_text = ""
            expect_frames = 0
            expect_bytes = 0
            recv_frames = 0
            recv_bytes = 0
            playback_active = False
            t0 = None

            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=90)
                if isinstance(msg, bytes):
                    # 用固件的解析函数校验下行帧
                    mt, seq, tshort, pcm = fw_decode_audio(msg)
                    assert mt == TYPE_DOWN, f"下行帧类型错误: {mt:#x}"
                    recv_frames += 1
                    recv_bytes += len(pcm)
                    if expect_frames and recv_frames >= expect_frames:
                        # 复刻固件：收齐即上报
                        await ws.send(fw_build_playback_done(turn_id, recv_bytes, recv_frames))
                        playback_active = False
                    continue

                data = json.loads(msg)
                t = data["type"]
                p = data  # 字段平铺，不嵌套在 payload 里

                if t == "asr_final":
                    asr_text = p.get("text", "")
                    print(f"  ASR: {asr_text}")
                elif t == "llm_delta":
                    reply_text += p.get("text", "")
                elif t == "llm_final":
                    print(f"  LLM: {reply_text or p.get('text','')}")
                elif t == "playback_start":
                    expect_frames = p.get("frames", 0)
                    expect_bytes = p.get("bytes", 0)
                    playback_active = True
                    print(f"  playback_start 预期 {expect_frames} 帧 / {expect_bytes} 字节")
                elif t == "error":
                    print(f"  错误: {p.get('code')} {p.get('message')}")
                    all_ok = False
                elif t == "turn_done":
                    print(f"  turn_done reason={p.get('reason')}")
                    break
                elif t == "turn_cancelled":
                    print(f"  turn_cancelled reason={p.get('reason')}")
                    break
                elif t == "heartbeat_ack":
                    continue

            # ---- 校验音频完整性（固件也会做同样的比对）----
            match = (recv_bytes == expect_bytes) and (recv_frames == expect_frames)
            print(f"  音频校验: 收到 {recv_frames}/{expect_frames} 帧, "
                  f"{recv_bytes}/{expect_bytes} 字节 → {'一致' if match else '不一致!'}")
            if not match:
                all_ok = False

    print("\n" + "=" * 64)
    print("  结论:", "固件协议实现与服务端完全兼容" if all_ok else "存在不一致，需修正")
    print("=" * 64)
    return 0 if all_ok else 1


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8765")
    ap.add_argument("--wav", default=str(Path(__file__).resolve().parent.parent /
                                        "tests" / "audio" / "asr_example.wav"))
    ap.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args()
    return asyncio.run(run(args.url, Path(args.wav), args.rounds))


if __name__ == "__main__":
    sys.exit(main())
