"""WebSocket 协议定义测试。

重点覆盖：
- 二进制帧编解码往返（帧头 struct 布局）
- 协议版本校验
- 消息大小限制
- 错误码可重试标记
- turn_id 压缩的稳定性
"""

from __future__ import annotations

import struct

import pytest

from server.protocol import (
    BINARY_HEADER_SIZE,
    BINARY_MAGIC,
    MAX_AUDIO_CHUNK_BYTES,
    MAX_TEXT_CHARS,
    PROTOCOL_VERSION,
    AudioFrame,
    BinaryType,
    Envelope,
    ErrorCode,
    ProtocolError,
    make_error,
    make_hello_ack,
    turn_id_to_short,
    validate_inbound,
    validate_text,
)
from server.protocol import messages as M


# ---------------------------------------------------------------------------
# 二进制帧
# ---------------------------------------------------------------------------


def test_audio_frame_roundtrip() -> None:
    payload = b"\x01\x02\x03\x04" * 40  # 160 字节
    frame = AudioFrame(
        msg_type=BinaryType.AUDIO_UP,
        seq=7,
        turn_id_short=0xDEADBEEF,
        payload=payload,
    )
    encoded = frame.encode()
    assert len(encoded) == BINARY_HEADER_SIZE + len(payload)
    assert encoded[0] == BINARY_MAGIC

    decoded = AudioFrame.decode(encoded)
    assert decoded.msg_type == BinaryType.AUDIO_UP
    assert decoded.seq == 7
    assert decoded.turn_id_short == 0xDEADBEEF
    assert decoded.payload == payload


def test_header_is_little_endian_12_bytes() -> None:
    """帧头必须是 12 字节小端，ESP32 端才能用同一 struct 解析。"""
    frame = AudioFrame(BinaryType.AUDIO_DOWN, 1, 2, b"abc")
    raw = frame.encode()
    magic, mtype, seq, turn, plen = struct.unpack("<BBHII", raw[:12])
    assert (magic, mtype, seq, turn, plen) == (BINARY_MAGIC, 2, 1, 2, 3)


def test_empty_payload_allowed() -> None:
    """空负载合法（例如结束标记）。"""
    frame = AudioFrame(BinaryType.AUDIO_UP, 0, 0, b"")
    decoded = AudioFrame.decode(frame.encode())
    assert decoded.payload == b""


def test_decode_too_short() -> None:
    with pytest.raises(ValueError, match="帧长度不足"):
        AudioFrame.decode(b"\xa5\x01\x00")


def test_decode_bad_magic() -> None:
    raw = AudioFrame(BinaryType.AUDIO_UP, 0, 0, b"x").encode()
    bad = b"\x00" + raw[1:]
    with pytest.raises(ValueError, match="魔数"):
        AudioFrame.decode(bad)


def test_decode_unknown_binary_type() -> None:
    raw = bytearray(AudioFrame(BinaryType.AUDIO_UP, 0, 0, b"x").encode())
    raw[1] = 0x7F
    with pytest.raises(ValueError, match="未知二进制类型"):
        AudioFrame.decode(bytes(raw))


def test_encode_rejects_oversized() -> None:
    frame = AudioFrame(BinaryType.AUDIO_UP, 0, 0, b"\x00" * (MAX_AUDIO_CHUNK_BYTES + 1))
    with pytest.raises(ValueError, match="音频块过大"):
        frame.encode()


def test_decode_rejects_oversized_declared() -> None:
    """帧头声明的长度超过上限必须拒绝，防止内存攻击。"""
    header = struct.pack("<BBHII", BINARY_MAGIC, 1, 0, 0, MAX_AUDIO_CHUNK_BYTES + 100)
    with pytest.raises(ValueError, match="负载声明过大"):
        AudioFrame.decode(header + b"\x00" * 10)


def test_decode_rejects_incomplete_payload() -> None:
    """声明长度大于实际数据必须拒绝。"""
    header = struct.pack("<BBHII", BINARY_MAGIC, 1, 0, 0, 100)
    with pytest.raises(ValueError, match="负载不完整"):
        AudioFrame.decode(header + b"\x00" * 10)


def test_seq_wraps_at_16bit() -> None:
    """seq 是 uint16，超出应回绕而不是报错。"""
    frame = AudioFrame(BinaryType.AUDIO_UP, 0x1_0000 + 5, 0, b"x")
    decoded = AudioFrame.decode(frame.encode())
    assert decoded.seq == 5


# ---------------------------------------------------------------------------
# turn_id 压缩
# ---------------------------------------------------------------------------


def test_turn_id_short_is_stable() -> None:
    """同一 turn_id 必须得到相同的短 ID（两端一致）。"""
    a = turn_id_to_short("sess1-t3")
    b = turn_id_to_short("sess1-t3")
    assert a == b
    assert 0 <= a <= 0xFFFFFFFF


def test_turn_id_short_differs() -> None:
    assert turn_id_to_short("t1") != turn_id_to_short("t2")


# ---------------------------------------------------------------------------
# 消息校验
# ---------------------------------------------------------------------------


def test_validate_inbound_ok() -> None:
    msg = {"type": "recording_start", "protocol_version": PROTOCOL_VERSION}
    assert validate_inbound(msg) == "recording_start"


def test_validate_inbound_not_dict() -> None:
    with pytest.raises(ProtocolError) as e:
        validate_inbound([])  # type: ignore[arg-type]
    assert e.value.code == ErrorCode.BAD_MESSAGE


def test_validate_inbound_missing_type() -> None:
    with pytest.raises(ProtocolError) as e:
        validate_inbound({"protocol_version": 1})
    assert e.value.code == ErrorCode.BAD_MESSAGE


def test_validate_inbound_unknown_type() -> None:
    with pytest.raises(ProtocolError) as e:
        validate_inbound({"type": "nope", "protocol_version": 1})
    assert e.value.code == ErrorCode.UNKNOWN_TYPE


def test_validate_inbound_missing_version() -> None:
    with pytest.raises(ProtocolError) as e:
        validate_inbound({"type": "recording_start"})
    assert e.value.code == ErrorCode.BAD_MESSAGE


def test_validate_inbound_version_mismatch() -> None:
    """版本不匹配必须检出——固件烧录后难改，这是关键防线。"""
    with pytest.raises(ProtocolError) as e:
        validate_inbound({"type": "recording_start", "protocol_version": 999})
    assert e.value.code == ErrorCode.PROTOCOL_VERSION_MISMATCH


def test_all_documented_types_accepted() -> None:
    """V2 第十一节列出的类型都应被接受。"""
    documented = [
        "device_hello", "session_start", "recording_start", "recording_stop",
        "asr_partial", "asr_final", "llm_start", "llm_delta", "llm_final",
        "tts_start", "playback_start", "playback_done", "turn_done",
        "error", "heartbeat",
    ]
    for t in documented:
        assert validate_inbound({"type": t, "protocol_version": 1}) == t


# ---------------------------------------------------------------------------
# 文本校验
# ---------------------------------------------------------------------------


def test_validate_text_ok() -> None:
    assert validate_text("你好") == "你好"


def test_validate_text_not_string() -> None:
    with pytest.raises(ProtocolError) as e:
        validate_text(123)
    assert e.value.code == ErrorCode.BAD_MESSAGE


def test_validate_text_too_long() -> None:
    with pytest.raises(ProtocolError) as e:
        validate_text("字" * (MAX_TEXT_CHARS + 1))
    assert e.value.code == ErrorCode.TEXT_TOO_LONG


# ---------------------------------------------------------------------------
# 消息构造
# ---------------------------------------------------------------------------


def test_envelope_omits_empty_fields() -> None:
    env = Envelope(type="heartbeat")
    d = env.to_dict()
    assert d["type"] == "heartbeat"
    assert d["protocol_version"] == PROTOCOL_VERSION
    assert "session_id" not in d
    assert "turn_id" not in d


def test_envelope_includes_identifiers() -> None:
    env = Envelope(type="llm_delta", session_id="s1", turn_id="t1", seq=3,
                   payload={"text": "你"})
    d = env.to_dict()
    assert d["session_id"] == "s1"
    assert d["turn_id"] == "t1"
    assert d["seq"] == 3
    assert d["text"] == "你"


def test_make_error_retryable_flag() -> None:
    """可重试错误必须标出来，固件据此决定是否重试。"""
    d = make_error(ErrorCode.TTS_FAILED, "合成失败")
    assert d["type"] == "error"
    assert d["code"] == ErrorCode.TTS_FAILED
    assert d["retryable"] is True
    assert d["fatal"] is False


def test_make_error_non_retryable() -> None:
    d = make_error(ErrorCode.TEXT_TOO_LONG, "文本过长")
    assert d["retryable"] is False


def test_make_error_fatal() -> None:
    d = make_error(ErrorCode.PROTOCOL_VERSION_MISMATCH, "版本不符", fatal=True)
    assert d["fatal"] is True


def test_hello_ack_carries_audio_format() -> None:
    """握手应答必须带音频格式，否则开发板不知道该发什么。"""
    d = make_hello_ack(
        server_name="ai-role",
        audio_in_sample_rate=16000,
        audio_in_channels=1,
        audio_out_sample_rate=24000,
    )
    assert d["type"] == "server_hello"
    assert d["audio_in"]["sample_rate"] == 16000
    assert d["audio_in"]["bits"] == 16
    assert d["audio_out"]["sample_rate"] == 24000
    assert d["limits"]["max_audio_chunk_bytes"] == MAX_AUDIO_CHUNK_BYTES


def test_binary_type_values() -> None:
    assert BinaryType.AUDIO_UP == 0x01
    assert BinaryType.AUDIO_DOWN == 0x02


def test_control_types_excludes_binary() -> None:
    """tts_audio 走二进制，不应出现在 JSON 类型集合里。"""
    assert "tts_audio" not in M.CONTROL_TYPES
    assert "audio_chunk" not in M.CONTROL_TYPES