"""流式 TTS 链路的测试。

背景（2026-11-12 延迟优化）：
用户反馈板子从说完话到听到回复有 4~5 秒延迟。日志分析显示：

    ASR   约 0.1s   （2%）
    LLM   约 1.2s   （24%）
    TTS   约 4.8s   （74%）  ← 绝对瓶颈

TTS 慢的原因不是算力，而是**非流式**：`list(model.inference_zero_shot(...))`
必须等整段音频合成完（8 秒音频要 4.8 秒）才返回，这期间板子完全静默。

改造：服务端改用 `/synthesize_stream`，CosyVoice 按句流式生成，
首块约 1~1.5 秒即可下发开播，播放期间后续音频继续合成。

实测对比（34 字文本，8 秒音频）：

    非流式首块: 5106 ms
    流式首块  : 1685 ms   → 省 3.42 秒（67%）

协议配套改动：流式下总帧数未知，`playback_start.frames` 传 0，
板子改为在收到「负载长度为 0 的二进制帧」时判定收尾。
旧固件只看 frames 会不报 playback_done，因此固件必须同步升级。

本文件锁住协议与实现的关键约束。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = PROJECT_ROOT / "server" / "ws" / "server.py"
TTS_SERVER = PROJECT_ROOT / "scripts" / "tts_server_wsl.py"
TTS_CLIENT = PROJECT_ROOT / "server" / "tts" / "client.py"
FIRMWARE = (
    PROJECT_ROOT.parent / "yuanma" / "AI-Pet-Badge-main"
    / "firmware" / "ai_pet_badge" / "main" / "voice_client.c"
)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


# ----------------------------------------------------------------------
# TTS 服务端：必须有流式接口
# ----------------------------------------------------------------------


def test_tts_server_has_stream_endpoint() -> None:
    src = _read(TTS_SERVER)
    assert "/synthesize_stream" in src, "TTS 服务端缺少流式接口"


def test_tts_server_uses_stream_true() -> None:
    """流式接口必须真的传 stream=True 给 CosyVoice。"""
    src = _read(TTS_SERVER)
    idx = src.index("/synthesize_stream")
    body = src[idx:idx + 3000]
    assert "stream=True" in body, (
        "流式接口没有传 stream=True，CosyVoice 仍会整段合成后才返回"
    )


def test_tts_server_sends_end_marker() -> None:
    """流式响应末尾必须发长度 0 的块作为结束标记。"""
    src = _read(TTS_SERVER)
    assert 'struct.pack(">I", 0)' in src, "缺少结束标记（长度 0 的块）"


# ----------------------------------------------------------------------
# TTS 客户端：解析长度前缀流
# ----------------------------------------------------------------------


def test_client_has_stream_method() -> None:
    src = _read(TTS_CLIENT)
    assert "async def synthesize_stream" in src


def test_client_records_stream_sample_rate() -> None:
    """采样率必须在读首块前拿到：调用方收到首块就要发 playback_start。"""
    src = _read(TTS_CLIENT)
    assert "last_stream_sample_rate" in src


def test_length_prefixed_parser() -> None:
    """验证长度前缀解析器能正确处理粘包与分片。"""
    from server.tts.client import _iter_length_prefixed

    async def byte_iter():
        # 故意把一帧拆成两段发，模拟网络分片
        payload = b"\x01\x02\x03\x04"
        frame = struct.pack(">I", len(payload)) + payload
        yield frame[:3]      # 头部被切断
        yield frame[3:] + struct.pack(">I", 0)   # 剩余 + 结束标记

    async def collect():
        out = []
        async for item in _iter_length_prefixed(byte_iter()):
            out.append(item)
        return out

    import asyncio
    items = asyncio.run(collect())
    assert items == [b"\x01\x02\x03\x04", None], (
        "解析器未正确处理分片与结束标记"
    )


def test_parser_detects_truncated_stream() -> None:
    """没有结束标记的流必须让调用方知道「被截断」，不能当成正常结束。"""
    from server.tts.client import _iter_length_prefixed

    async def byte_iter():
        payload = b"\xaa\xbb"
        yield struct.pack(">I", len(payload)) + payload
        # 故意不发结束标记

    async def collect():
        out = []
        async for item in _iter_length_prefixed(byte_iter()):
            out.append(item)
        return out

    import asyncio
    items = asyncio.run(collect())
    assert None not in items, "截断的流不应产出结束标记"


# ----------------------------------------------------------------------
# WS 服务端：流式下发
# ----------------------------------------------------------------------


def test_ws_server_uses_streaming() -> None:
    src = _read(SERVER_PY)
    assert "synthesize_stream" in src, "WS 服务端没有使用流式 TTS"


def test_ws_server_sends_frames_zero() -> None:
    """流式下 playback_start 的 frames 必须是 0（表示长度未知）。

    注意：2026-11-12 加填充音后，playback_start 可能在两处发
    （填充音会话 / 无填充音时），所以搜索窗口要足够大。
    """
    src = _read(SERVER_PY)
    idx = src.index("synthesize_stream")
    body = src[idx:idx + 8000]
    assert '"frames": 0' in body, (
        "流式模式必须把 frames 置 0，否则板子会按旧逻辑等固定帧数"
    )
    assert '"streaming": True' in body, "缺少 streaming 标记"


def test_ws_server_sends_end_marker_frame() -> None:
    """下发结束后必须发一个空负载帧作为结束标记。

    2026-11-25：发送动作收敛到 ``_send_end_marker()`` 里，
    以便异常路径也能补发（原来只有成功路径发，导致板子唯一的
    无条件兜底 ``finish_playback_force()`` 在异常路径上永久不可达）。
    这里改为语义断言：必须存在一个发送空负载帧的辅助函数，
    且成功路径与各异常路径都要调用它。
    """
    src = _read(SERVER_PY)
    # 1) 辅助函数存在，且确实发的是空负载
    assert "async def _send_end_marker" in src, "缺少结束标记帧的发送辅助函数"
    idx = src.index("async def _send_end_marker")
    helper = src[idx:idx + 1600]
    assert 'send_audio(client, b""' in helper, (
        "结束标记辅助函数没有发送空负载帧"
    )
    # 2) 成功路径要发
    assert "await self._send_end_marker(client)" in src, (
        "成功路径没有发结束标记帧，板子无法判断音频结束"
    )
    # 3) 异常路径也要补发（TTS 失败 / 本轮超时 / 其它异常 / 播放看门狗）
    assert src.count("await self._send_end_marker(client)") >= 5, (
        "异常收尾路径没有补发结束标记帧："
        "板子唯一的无条件兜底 finish_playback_force() 将永久不可达"
    )
    # 4) 幂等：同一轮不能重复补发
    assert "end_marker_sent" in helper, "结束标记帧没有幂等保护"


# ----------------------------------------------------------------------
# 固件：支持流式收尾
# ----------------------------------------------------------------------


def test_firmware_handles_end_marker() -> None:
    """固件必须处理「负载长度为 0」的帧作为结束信号。"""
    if not FIRMWARE.is_file():
        pytest.skip("固件源码不在预期位置")
    src = _read(FIRMWARE)
    assert "s_got_end_marker" in src, "固件没有流式结束标记的变量"
    assert "if (plen == 0)" in src, "固件没有判断空负载帧"


def test_firmware_keeps_backward_compat() -> None:
    """必须保留旧的定长路径（frames>0），否则旧服务端无法工作。"""
    src = _read(FIRMWARE)
    assert "s_expect_frames && s_recv_frames >= s_expect_frames" in src, (
        "定长模式的收尾判断被删了，旧服务端会无法收尾"
    )


def test_firmware_has_idle_watchdog() -> None:
    """流式下若服务端漏发结束标记，固件要有兜底，不能永久卡 PLAYING。"""
    src = _read(FIRMWARE)
    assert "s_idle_ticks" in src, "固件缺少空闲计数"
    assert "强制收尾" in src, "固件缺少断流兜底逻辑"


def test_firmware_volume_default_raised() -> None:
    """固件默认音量应为 70（2026-11-23 由 85 下调）。

    历史：60 在板载小喇叭上偏轻 → 提到 85 避免削波破音。
    实测 85 对当前角色素材偏响，用户确认后固化到 70。
    """
    src = _read(FIRMWARE)
    assert "s_volume = 70" in src, (
        "固件默认音量不是 70；85 偏响，60 偏轻"
    )
