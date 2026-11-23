"""Phase 5 测试：zjy13 协议桥接。

重点验证「与 zjy13 的契约完全一致」——这是不重复造轮子的前提。
测试会直接读 zjy13 源码比对常量，防止两边漂移。
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest

from custom_voice.bridge.protocol import (
    BINARY_HEADER_SIZE,
    BINARY_MAGIC,
    BINARY_TYPE_TTS,
    BYTES_PER_SEC,
    DEFAULT_FRAME_BYTES,
    FIRST_BURST_MS,
    MAX_AUDIO_CHUNK_BYTES,
    SAMPLE_RATE,
    BackpressureState,
    decode_binary_frame,
    describe_compat,
    encode_binary_frame,
    encode_length_prefixed,
    encode_stream_end,
    iter_frames,
)
from custom_voice.bridge.stream import FakeDevice

ZJY13 = Path("/mnt/d/xiangmudaima/zjy13")
HAS_ZJY13 = ZJY13.is_dir()

requires_zjy13 = pytest.mark.skipif(not HAS_ZJY13, reason="zjy13 仓库不在预期位置")


# ----------------------------------------------------------------------
# 二进制帧格式
# ----------------------------------------------------------------------


def test_frame_header_is_12_bytes() -> None:
    frame = encode_binary_frame(b"\x01\x02", seq=0)
    assert len(frame) == BINARY_HEADER_SIZE + 2


def test_frame_magic() -> None:
    frame = encode_binary_frame(b"", seq=0)
    assert frame[0] == BINARY_MAGIC == 0xA5


def test_frame_type_is_tts_downstream() -> None:
    frame = encode_binary_frame(b"", seq=0)
    assert frame[1] == BINARY_TYPE_TTS == 0x02


def test_frame_roundtrip() -> None:
    payload = b"\x10\x20\x30\x40"
    frame = encode_binary_frame(payload, seq=7, turn_id_short=12345)
    d = decode_binary_frame(frame)
    assert d["magic"] == 0xA5
    assert d["msg_type"] == 0x02
    assert d["seq"] == 7
    assert d["turn_id_short"] == 12345
    assert d["payload"] == payload
    assert d["payload_len"] == 4


def test_seq_wraps_at_uint16() -> None:
    """seq 是 uint16，超出应回绕而不是报错。"""
    frame = encode_binary_frame(b"x", seq=70000)
    d = decode_binary_frame(frame)
    assert d["seq"] == 70000 & 0xFFFF


def test_payload_too_large_rejected() -> None:
    with pytest.raises(ValueError) as ei:
        encode_binary_frame(b"\x00" * (MAX_AUDIO_CHUNK_BYTES + 1), seq=0)
    assert "过大" in str(ei.value)


def test_decode_rejects_short_frame() -> None:
    with pytest.raises(ValueError) as ei:
        decode_binary_frame(b"\xA5\x02")
    assert "过短" in str(ei.value)


def test_decode_rejects_bad_magic() -> None:
    bad = struct.pack("<BBHII", 0x00, 0x02, 0, 0, 0)
    with pytest.raises(ValueError) as ei:
        decode_binary_frame(bad)
    assert "magic" in str(ei.value)


def test_decode_rejects_truncated_payload() -> None:
    frame = encode_binary_frame(b"\x00" * 100, seq=0)
    with pytest.raises(ValueError) as ei:
        decode_binary_frame(frame[:50])
    assert "不完整" in str(ei.value)


def test_frame_is_little_endian() -> None:
    """协议明确要求小端。"""
    frame = encode_binary_frame(b"", seq=0x0102)
    # 头部偏移 2 起是 seq（uint16 小端）
    seq_bytes = frame[2:4]
    assert seq_bytes == b"\x02\x01"


# ----------------------------------------------------------------------
# HTTP 流式格式
# ----------------------------------------------------------------------


def test_length_prefix_is_big_endian() -> None:
    """zjy13 用 struct.pack(">I", ...)，大端。"""
    data = encode_length_prefixed(b"abcd")
    assert data[:4] == b"\x00\x00\x00\x04"
    assert data[4:] == b"abcd"


def test_stream_end_marker_is_zero_length() -> None:
    assert encode_stream_end() == b"\x00\x00\x00\x00"


# ----------------------------------------------------------------------
# 分块上限（2026-11-23 修复回归锁）
# ----------------------------------------------------------------------


def _parse_length_prefixed(stream: bytes) -> list[int]:
    """把「长度前缀流」解析成各块长度列表（不消费结束标记）。"""
    sizes = []
    pos = 0
    while pos + 4 <= len(stream):
        (n,) = struct.unpack(">I", stream[pos:pos + 4])
        pos += 4
        if n == 0:
            break
        sizes.append(n)
        pos += n
    return sizes


def test_encode_splits_oversized_pcm() -> None:
    """超过协议上限的 PCM 必须被切成多块。

    回归锁：修复前 ``encode_length_prefixed`` 直接整块加长度前缀，
    引擎按句 yield 的 192000 字节会原样下发，超出 48KB 上限，
    导致 zjy13 的 TTSClient 抛「流式分块长度异常」、板子播放中止。
    """
    from custom_voice.bridge.protocol import MAX_AUDIO_CHUNK_BYTES

    pcm = b"\x01\x00" * 96000  # 192000 字节，正是实测报错尺寸
    assert len(pcm) > MAX_AUDIO_CHUNK_BYTES

    out = encode_length_prefixed(pcm)
    sizes = _parse_length_prefixed(out)

    assert len(sizes) > 1, f"192000 字节没有被切块，仍是 {len(sizes)} 块"
    assert all(s <= MAX_AUDIO_CHUNK_BYTES for s in sizes), (
        f"存在超过上限的块: max={max(sizes)} > {MAX_AUDIO_CHUNK_BYTES}"
    )
    assert sum(sizes) == len(pcm), "切块后字节总数不一致（有丢失或重复）"


def test_encode_keeps_small_pcm_as_single_chunk() -> None:
    """未超限的 PCM 不应被无谓拆分（避免增加帧数与延迟）。"""
    pcm = b"\x01\x00" * 100  # 200 字节
    out = encode_length_prefixed(pcm)
    sizes = _parse_length_prefixed(out)
    assert sizes == [len(pcm)]


def test_encode_chunks_are_int16_aligned() -> None:
    """切块长度必须是偶数，否则会把 int16 采样切断。"""
    from custom_voice.bridge.protocol import MAX_AUDIO_CHUNK_BYTES

    pcm = b"\x00\x01" * 100000
    sizes = _parse_length_prefixed(encode_length_prefixed(pcm))
    assert all(s % 2 == 0 for s in sizes), f"存在奇数字节块: {sizes}"
    assert all(s <= MAX_AUDIO_CHUNK_BYTES for s in sizes)


def test_encode_rejects_odd_chunk_size() -> None:
    """非法上限（奇数/非正）必须显式报错，不能静默产生错位音频。"""
    for bad in (0, -1, 4095):
        with pytest.raises(ValueError):
            encode_length_prefixed(b"\x00\x00", max_chunk_bytes=bad)


def test_length_prefixed_roundtrip() -> None:
    pcm1 = b"\x01\x02\x03"
    pcm2 = b"\x04\x05"
    stream = encode_length_prefixed(pcm1) + encode_length_prefixed(pcm2) + encode_stream_end()

    out = []
    pos = 0
    while pos + 4 <= len(stream):
        n = struct.unpack(">I", stream[pos:pos + 4])[0]
        pos += 4
        if n == 0:
            out.append(None)
            break
        out.append(stream[pos:pos + n])
        pos += n

    assert out == [pcm1, pcm2, None]


# ----------------------------------------------------------------------
# 帧切分
# ----------------------------------------------------------------------


def test_first_frame_meets_burst_requirement() -> None:
    """首帧必须至少 FIRST_BURST_MS 毫秒音频。

    依据 zjy13 ws/server.py:1100：固件要攒够预缓冲才开播，
    首块太小会导致"更晚才出声"。
    """
    # 构造 1 秒音频，分 10 块喂入
    total = BYTES_PER_SEC  # 1 秒
    chunks = [b"\x00" * (total // 10) for _ in range(10)]
    frames, stats = iter_frames(chunks, frame_bytes=DEFAULT_FRAME_BYTES)

    expected_min = BYTES_PER_SEC * FIRST_BURST_MS // 1000
    assert stats.first_frame_bytes >= expected_min, (
        f"首帧 {stats.first_frame_bytes} 字节，"
        f"应至少 {expected_min}（{FIRST_BURST_MS}ms）"
    )


def test_subsequent_frames_use_frame_bytes() -> None:
    total = BYTES_PER_SEC  # 1 秒
    chunks = [b"\x00" * (total // 4) for _ in range(4)]
    frames, stats = iter_frames(chunks, frame_bytes=DEFAULT_FRAME_BYTES)

    assert stats.frames >= 2
    # 除首帧与尾帧外，都应是 frame_bytes
    middle = [
        len(f) - BINARY_HEADER_SIZE for f in frames[1:-1]
    ]
    assert all(b == DEFAULT_FRAME_BYTES for b in middle)


def test_no_tiny_tail_frame() -> None:
    """余量应并入最后一帧，不产生过小的尾帧。"""
    # 故意构造不能被 frame_bytes 整除的长度
    total = DEFAULT_FRAME_BYTES * 3 + 100
    frames, stats = iter_frames([b"\x00" * total], frame_bytes=DEFAULT_FRAME_BYTES)
    last = len(frames[-1]) - BINARY_HEADER_SIZE
    # 尾帧应是余量（100）或与中间帧合并后更大，不应为 0
    assert last > 0


def test_total_bytes_preserved() -> None:
    """切分不能丢数据或加数据。"""
    total = 50000
    chunks = [b"\xAB" * 12345 for _ in range(4)]  # 49380
    chunks.append(b"\xCD" * (total - 49380))
    frames, stats = iter_frames(chunks, frame_bytes=DEFAULT_FRAME_BYTES)
    assert stats.total_bytes == total


def test_empty_input_yields_no_frames() -> None:
    frames, stats = iter_frames([], frame_bytes=DEFAULT_FRAME_BYTES)
    assert frames == []
    assert stats.frames == 0


def test_all_zero_chunks_skipped() -> None:
    frames, stats = iter_frames([b"", b"", b""], frame_bytes=DEFAULT_FRAME_BYTES)
    assert frames == []


def test_invalid_frame_bytes_rejected() -> None:
    with pytest.raises(ValueError):
        iter_frames([b"x"], frame_bytes=0)


def test_seq_increments() -> None:
    total = DEFAULT_FRAME_BYTES * 4
    frames, _ = iter_frames([b"\x00" * total], frame_bytes=DEFAULT_FRAME_BYTES)
    seqs = [decode_binary_frame(f)["seq"] for f in frames]
    assert seqs == list(range(len(frames)))


# ----------------------------------------------------------------------
# 背压
# ----------------------------------------------------------------------


def test_backpressure_first_burst_no_wait() -> None:
    """首块阶段不应节流。"""
    bp = BackpressureState()
    wait = bp.should_wait(played_sec=0.0, next_piece_bytes=bp.first_burst_bytes)
    assert wait == 0.0


def test_backpressure_waits_when_ahead() -> None:
    bp = BackpressureState(lead_sec=0.8)
    bp.mark_sent(bp.first_burst_bytes)  # 越过首块
    # 已发送 10 秒音频，但只播了 0 秒 -> 领先 10s，远超 0.8s
    bp.sent_bytes = BYTES_PER_SEC * 10
    wait = bp.should_wait(played_sec=0.0, next_piece_bytes=1000)
    assert wait > 0


def test_backpressure_no_wait_when_caught_up() -> None:
    bp = BackpressureState(lead_sec=0.8)
    bp.sent_bytes = BYTES_PER_SEC * 2
    # 已播 2 秒，领先 0 秒
    wait = bp.should_wait(played_sec=2.0, next_piece_bytes=1000)
    assert wait == 0.0


# ----------------------------------------------------------------------
# 模拟设备
# ----------------------------------------------------------------------


def test_fake_device_starts_after_prebuffer() -> None:
    """设备要攒够预缓冲才开播（固件 APP_PLAYBACK_RING_MS=200）。"""
    dev = FakeDevice(ring_ms=200)
    ring_bytes = BYTES_PER_SEC * 200 // 1000

    # 喂一帧不够预缓冲的
    dev.feed(encode_binary_frame(b"\x00" * 100, seq=0))
    assert dev.started is False

    # 再喂足够字节
    dev.feed(encode_binary_frame(b"\x00" * ring_bytes, seq=1))
    assert dev.started is True


def test_fake_device_detects_end_marker() -> None:
    dev = FakeDevice()
    dev.feed(encode_binary_frame(b"\x00" * 1000, seq=0))
    dev.feed(encode_binary_frame(b"", seq=1))
    assert dev.got_end is True


def test_fake_device_detects_seq_gap() -> None:
    dev = FakeDevice()
    dev.feed(encode_binary_frame(b"\x00" * 100, seq=0))
    dev.feed(encode_binary_frame(b"\x00" * 100, seq=5))  # 跳变
    s = dev.summary()
    assert s["seq_errors"], "应检出序号跳变"
    assert "跳变" in s["seq_errors"][0]


def test_fake_device_accepts_sequential() -> None:
    dev = FakeDevice()
    for i in range(5):
        dev.feed(encode_binary_frame(b"\x00" * 100, seq=i))
    assert dev.summary()["seq_errors"] == []


def test_fake_device_full_flow() -> None:
    """完整流程：多帧 + 结束标记。"""
    total = BYTES_PER_SEC  # 1 秒
    frames, stats = iter_frames([b"\x00" * total], frame_bytes=DEFAULT_FRAME_BYTES)

    dev = FakeDevice(ring_ms=200)
    for f in frames:
        dev.feed(f)
    dev.feed(encode_binary_frame(b"", seq=stats.frames))

    s = dev.summary()
    assert s["got_end"] is True
    assert s["started"] is True
    assert s["seq_errors"] == []
    assert s["played_bytes"] + s["pending_bytes"] == stats.total_bytes


# ----------------------------------------------------------------------
# 与 zjy13 源码的一致性（关键：防止协议漂移）
# ----------------------------------------------------------------------


@requires_zjy13
def test_magic_matches_zjy13() -> None:
    src = (ZJY13 / "server/protocol/messages.py").read_text(encoding="utf-8")
    assert "BINARY_MAGIC = 0xA5" in src
    assert BINARY_MAGIC == 0xA5


@requires_zjy13
def test_header_size_matches_zjy13() -> None:
    src = (ZJY13 / "server/protocol/messages.py").read_text(encoding="utf-8")
    assert "BINARY_HEADER_SIZE = 12" in src
    assert BINARY_HEADER_SIZE == 12


@requires_zjy13
def test_chunk_limit_matches_zjy13() -> None:
    src = (ZJY13 / "server/protocol/messages.py").read_text(encoding="utf-8")
    assert "MAX_AUDIO_CHUNK_BYTES = 48 * 1024" in src
    assert MAX_AUDIO_CHUNK_BYTES == 48 * 1024


@requires_zjy13
def test_frame_bytes_matches_zjy13() -> None:
    src = (ZJY13 / "server/ws/server.py").read_text(encoding="utf-8")
    assert "step = 4096" in src
    assert DEFAULT_FRAME_BYTES == 4096


@requires_zjy13
def test_first_burst_matches_zjy13() -> None:
    src = (ZJY13 / "server/ws/server.py").read_text(encoding="utf-8")
    assert "FIRST_BURST_MS = 300" in src
    assert FIRST_BURST_MS == 300


@requires_zjy13
def test_stream_format_matches_zjy13() -> None:
    """HTTP 流式格式必须与 zjy13 一致：大端长度前缀 + 0 结束。

    注意：不要断言具体变量名。zjy13 侧 2026-11-23 因协议 48KB 上限
    改成按块切分发送（``pcm`` -> ``piece``），格式未变但字面量变了。
    这里只校验「4 字节大端长度前缀 + 负载」这个结构本身。
    """
    src = (ZJY13 / "scripts/tts_server_wsl.py").read_text(encoding="utf-8")
    # 长度前缀必须是大端 uint32（">I"），且与负载拼接
    assert re.search(r'struct\.pack\(\s*">I"\s*,\s*len\(\w+\)\s*\)\s*\+\s*\w+', src), (
        "zjy13 流式分块不再是「4 字节大端长度 + 负载」格式"
    )
    # 结束标记必须是长度 0
    assert re.search(r'struct\.pack\(\s*">I"\s*,\s*0\s*\)', src), (
        "zjy13 流式结束标记不是长度 0"
    )


@requires_zjy13
def test_stream_path_matches_zjy13() -> None:
    """zjy14 的流式路径必须与 zjy13 TTSClient 期望的一致。"""
    src = (ZJY13 / "server/tts/client.py").read_text(encoding="utf-8")
    assert 'stream_path: str = "/synthesize_stream"' in src
    assert describe_compat()["http_stream_path"] == "/synthesize_stream"


@requires_zjy13
def test_sample_rate_matches_firmware() -> None:
    """固件播放侧固定 24kHz，zjy14 输出必须一致，否则要重采样。"""
    src = (ZJY13 / "firmware/main/app_audio.h").read_text(encoding="utf-8")
    assert "24k" in src.lower() or "24000" in src
    assert SAMPLE_RATE == 24000


# ----------------------------------------------------------------------
# 兼容性描述
# ----------------------------------------------------------------------


def test_describe_compat_shape() -> None:
    d = describe_compat()
    for k in (
        "protocol_version",
        "binary_magic",
        "binary_header_size",
        "frame_bytes",
        "sample_rate",
        "first_burst_ms",
        "http_stream_path",
    ):
        assert k in d


def test_describe_compat_values() -> None:
    d = describe_compat()
    assert d["protocol_version"] == 1
    assert d["binary_magic"] == "0xA5"
    assert d["binary_header_size"] == 12
    assert d["frame_bytes"] == 4096
    assert d["sample_rate"] == 24000
    assert d["first_burst_ms"] == 300


def test_describe_compat_has_sources() -> None:
    """必须记录每个常量的来源文件，便于追溯。"""
    d = describe_compat()
    assert "sources" in d
    assert len(d["sources"]) >= 4
