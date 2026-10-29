"""固件源码与 Python 协议的一致性校验。

背景：ESP32 固件与 Python 服务端共用一份协议，但两边是独立代码。
任何一侧改了常量而另一侧没跟上，联调时就会出现"能连上但收不到音频"
这类难查的问题。本测试把关键常量与实现从 C 源码里读出来逐项比对。

这些测试**不需要烧板子**，纯静态校验，可在 CI 里跑。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server.protocol import messages as M

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FW_MAIN = PROJECT_ROOT / "firmware" / "main"


def _read(name: str) -> str:
    p = FW_MAIN / name
    assert p.is_file(), f"固件源文件缺失: {p}"
    return p.read_text(encoding="utf-8")


def _c_int(text: str, macro: str) -> int:
    """从 C 头文件里取 ``#define MACRO 123`` 的数值（支持 0x 十六进制）。"""
    m = re.search(
        rf"#define\s+{re.escape(macro)}\s+\(?\s*(0x[0-9A-Fa-f]+|[0-9]+)",
        text,
    )
    assert m, f"未找到宏 {macro}"
    return int(m.group(1), 0)


def _c_str(text: str, macro: str) -> str:
    """取 ``#define MACRO "value"`` 的字符串值。"""
    m = re.search(rf'#define\s+{re.escape(macro)}\s+"([^"]*)"', text)
    assert m, f"未找到字符串宏 {macro}"
    return m.group(1)


# ----------------------------------------------------------------------
# 协议常量一致性
# ----------------------------------------------------------------------


def test_binary_header_size_matches() -> None:
    cfg = _read("app_config.h")
    assert _c_int(cfg, "APP_BINARY_HEADER_SIZE") == M.BINARY_HEADER_SIZE


def test_binary_magic_matches() -> None:
    cfg = _read("app_config.h")
    assert _c_int(cfg, "APP_BINARY_MAGIC") == M.BINARY_MAGIC


def test_protocol_version_matches() -> None:
    cfg = _read("app_config.h")
    assert _c_int(cfg, "APP_PROTOCOL_VERSION") == M.PROTOCOL_VERSION


def test_binary_types_match() -> None:
    cfg = _read("app_config.h")
    assert _c_int(cfg, "APP_BIN_TYPE_AUDIO_UP") == M.BinaryType.AUDIO_UP.value
    assert _c_int(cfg, "APP_BIN_TYPE_AUDIO_DOWN") == M.BinaryType.AUDIO_DOWN.value


def test_max_json_bytes_matches() -> None:
    cfg = _read("app_config.h")
    # 固件写成 (64 * 1024)，用乘法形式；单独处理
    assert "64 * 1024" in cfg
    assert M.MAX_JSON_BYTES == 64 * 1024


def test_max_audio_chunk_matches() -> None:
    cfg = _read("app_config.h")
    assert "48 * 1024" in cfg
    assert M.MAX_AUDIO_CHUNK_BYTES == 48 * 1024


def test_max_text_chars_matches() -> None:
    cfg = _read("app_config.h")
    assert _c_int(cfg, "APP_MAX_TEXT_CHARS") == M.MAX_TEXT_CHARS


def test_timeouts_match() -> None:
    """心跳与单轮超时必须与服务端一致。"""
    cfg = _read("app_config.h")
    assert _c_int(cfg, "APP_HEARTBEAT_INTERVAL_MS") == 20000
    assert _c_int(cfg, "APP_HEARTBEAT_TIMEOUT_MS") == 30000
    assert _c_int(cfg, "APP_TURN_TIMEOUT_MS") == 60000


# ----------------------------------------------------------------------
# 消息类型字符串一致性
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "macro,expected",
    [
        ("APP_MSG_DEVICE_HELLO", M.DEVICE_HELLO),
        ("APP_MSG_SERVER_HELLO", M.SERVER_HELLO),
        ("APP_MSG_SESSION_START", M.SESSION_START),
        ("APP_MSG_RECORDING_START", M.RECORDING_START),
        ("APP_MSG_RECORDING_STOP", M.RECORDING_STOP),
        ("APP_MSG_ASR_FINAL", M.ASR_FINAL),
        ("APP_MSG_LLM_START", M.LLM_START),
        ("APP_MSG_LLM_DELTA", M.LLM_DELTA),
        ("APP_MSG_LLM_FINAL", M.LLM_FINAL),
        ("APP_MSG_TTS_START", M.TTS_START),
        ("APP_MSG_PLAYBACK_START", M.PLAYBACK_START),
        ("APP_MSG_PLAYBACK_DONE", M.PLAYBACK_DONE),
        ("APP_MSG_TURN_DONE", M.TURN_DONE),
        ("APP_MSG_CANCEL", M.CANCEL),
        ("APP_MSG_TURN_CANCELLED", M.TURN_CANCELLED),
        ("APP_MSG_ERROR", M.ERROR),
        ("APP_MSG_HEARTBEAT", M.HEARTBEAT),
        ("APP_MSG_HEARTBEAT_ACK", M.HEARTBEAT_ACK),
    ],
)
def test_message_type_matches(macro: str, expected: str) -> None:
    proto = _read("app_protocol.h")
    assert _c_str(proto, macro) == expected


@pytest.mark.parametrize(
    "macro,expected",
    [
        ("APP_ERR_PROTOCOL_VERSION_MISMATCH", M.ErrorCode.PROTOCOL_VERSION_MISMATCH),
        ("APP_ERR_BAD_MESSAGE", M.ErrorCode.BAD_MESSAGE),
        ("APP_ERR_NOT_HELLO", M.ErrorCode.NOT_HELLO),
        ("APP_ERR_TURN_BUSY", M.ErrorCode.TURN_BUSY),
        ("APP_ERR_AUDIO_TOO_LARGE", M.ErrorCode.AUDIO_TOO_LARGE),
        ("APP_ERR_AUDIO_BUFFER_OVERFLOW", M.ErrorCode.AUDIO_BUFFER_OVERFLOW),
        ("APP_ERR_TEXT_TOO_LONG", M.ErrorCode.TEXT_TOO_LONG),
        ("APP_ERR_ASR_FAILED", M.ErrorCode.ASR_FAILED),
        ("APP_ERR_ASR_EMPTY", M.ErrorCode.ASR_EMPTY),
        ("APP_ERR_LLM_FAILED", M.ErrorCode.LLM_FAILED),
        ("APP_ERR_TTS_FAILED", M.ErrorCode.TTS_FAILED),
        ("APP_ERR_TTS_UNAVAILABLE", M.ErrorCode.TTS_UNAVAILABLE),
        ("APP_ERR_CANCELLED", M.ErrorCode.CANCELLED),
        ("APP_ERR_INTERNAL", M.ErrorCode.INTERNAL),
    ],
)
def test_error_code_matches(macro: str, expected: str) -> None:
    proto = _read("app_protocol.h")
    assert _c_str(proto, macro) == expected


# ----------------------------------------------------------------------
# 音频格式一致性
# ----------------------------------------------------------------------


def test_audio_format_matches_server_expectation() -> None:
    """固件的采集格式必须与服务端默认期望一致。"""
    board = _read("app_board.h")
    assert _c_int(board, "APP_MIC_SAMPLE_RATE") == 16000
    assert _c_int(board, "APP_MIC_CHANNELS") == 4
    assert _c_str(board, "APP_MIC_LAYOUT") == "RMNM"


def test_playback_rate_matches_tts_output() -> None:
    """播放采样率必须等于 CosyVoice 输出采样率（24000），否则要重采样。"""
    board = _read("app_board.h")
    assert _c_int(board, "APP_SPK_SAMPLE_RATE") == 24000


def test_chunk_ms_in_protocol_recommended_range() -> None:
    """分块时长必须在协议推荐区间内。"""
    board = _read("app_board.h")
    chunk = _c_int(board, "APP_AUDIO_CHUNK_MS")
    assert min(M.RECOMMENDED_CHUNK_MS) <= chunk <= max(M.RECOMMENDED_CHUNK_MS)
    assert chunk in M.RECOMMENDED_CHUNK_MS


def test_chunk_bytes_within_limit() -> None:
    """40ms @ 16k 4ch 16bit 的实际字节数不能超过协议上限。"""
    board = _read("app_board.h")
    sr = _c_int(board, "APP_MIC_SAMPLE_RATE")
    ch = _c_int(board, "APP_MIC_CHANNELS")
    ms = _c_int(board, "APP_AUDIO_CHUNK_MS")
    chunk_bytes = sr * ch * 2 * ms // 1000
    assert chunk_bytes <= M.MAX_AUDIO_CHUNK_BYTES
    assert chunk_bytes == 5120, f"预期 5120 字节，实际 {chunk_bytes}"


# ----------------------------------------------------------------------
# 实现细节校验
# ----------------------------------------------------------------------


def test_crc32_table_used() -> None:
    """固件必须用标准 CRC-32（0xEDB88320），否则 turn_id 匹配不上服务端。"""
    proto = _read("app_protocol.c")
    assert "0xEDB88320" in proto
    assert "app_proto_turn_short" in proto


def test_binary_header_is_12_bytes() -> None:
    """结构体必须正好 12 字节（有 _Static_assert 兜底）。"""
    proto = _read("app_protocol.h")
    assert "_Static_assert(sizeof(app_bin_header_t) == 12" in proto


def test_endofprompt_not_needed_in_firmware() -> None:
    """固件不碰 TTS，不需要 endofprompt 前缀。

    这条测试是为了防止有人把 TTS 的知识误抄进固件。
    """
    proto = _read("app_protocol.c")
    assert "endofprompt" not in proto


def test_playback_done_reports_bytes() -> None:
    """playback_done 必须上报 received_bytes / received_frames。

    服务端靠它校验音频完整性；缺了字段会导致静默丢帧不可检测。
    """
    proto = _read("app_protocol.c")
    assert "received_bytes" in proto
    assert "received_frames" in proto


def test_playback_waits_for_drain() -> None:
    """上报 playback_done 前必须等 DMA 播完。

    踩过的坑：把"写入完成"当"播放完成"会导致服务端提前开下一轮。
    """
    main = _read("main.c")
    assert "app_audio_play_drain" in main
    # drain 必须在 build_playback_done 之前调用
    drain_pos = main.index("app_audio_play_drain")
    done_pos = main.index("app_proto_build_playback_done")
    assert drain_pos < done_pos, "必须先 drain 再上报 playback_done"


def test_half_duplex_enforced() -> None:
    """半双工：录音前关播放、播放前停录音。"""
    audio = _read("app_audio.c")
    # record_start 里应关闭功放
    rec = re.search(r"esp_err_t app_audio_record_start\(void\).*?\n\}", audio, re.S)
    assert rec, "未找到 app_audio_record_start"
    assert "APP_AUDIO_PA" in rec.group(0), "录音时没有关闭功放"

    # play_start 里应停止录音
    play = re.search(r"esp_err_t app_audio_play_start\(void\).*?\n\}", audio, re.S)
    assert play, "未找到 app_audio_play_start"
    assert "s_recording" in play.group(0), "播放前没有停止录音"


def test_eof_prompt_busy_rejected() -> None:
    """忙碌时必须拒绝新的录音请求，与服务端的 turn_busy 语义一致。"""
    main = _read("main.c")
    start = re.search(r"static void start_recording\(void\).*?\n\}", main, re.S)
    assert start
    assert "app_state_busy()" in start.group(0)


# ----------------------------------------------------------------------
# 固件工程结构
# ----------------------------------------------------------------------


def test_required_source_files_exist() -> None:
    for name in (
        "main.c",
        "app_board.h",
        "app_config.h",
        "app_protocol.h",
        "app_protocol.c",
        "app_net.h",
        "app_net.c",
        "app_audio.h",
        "app_audio.c",
        "app_ui.h",
        "app_ui.c",
        "app_state.h",
        "app_state.c",
        "CMakeLists.txt",
        "idf_component.yml",
        "Kconfig.projbuild",
    ):
        assert (FW_MAIN / name).is_file(), f"缺少固件文件: {name}"


def test_partition_table_fits_16mb() -> None:
    """分区不能超出 16MB Flash。"""
    csv = (PROJECT_ROOT / "firmware" / "partitions.csv").read_text(encoding="utf-8")
    total = 16 * 1024 * 1024
    for line in csv.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        offset = int(parts[3], 16)
        size = int(parts[4], 16)
        assert offset + size <= total, f"分区 {parts[0]} 超出 Flash: {hex(offset+size)}"


def test_websocket_component_declared() -> None:
    """WebSocket 组件必须声明为依赖，否则链接失败。"""
    yml = _read("idf_component.yml")
    assert "esp_websocket_client" in yml
    cmake = _read("CMakeLists.txt")
    assert "esp_websocket_client" in cmake
