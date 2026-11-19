"""本轮集成修复的回归锁（2026-11-19）。

覆盖五项，每项都对应一个曾经真实存在、且**难以发现**的缺陷：

1. turn_id 每 100 秒重复 —— 导致去重误拒 + 固件轮次校验串轮
2. `_completed_turns` 无界增长 —— 长会话内存泄漏
3. 422 未映射为参数错误 —— 不可重试的错误被当可重试，白白重试
4. 流式分块长度无上限 —— 损坏长度会让缓冲区无界增长直到 120s 超时
5. `pause_profile` 未显式传递 —— 升级引擎后韵律悄悄改变

这些缺陷的共同点是「不报错、只是行为不对」，所以必须有测试钉住。
"""

from __future__ import annotations

import asyncio
import io
import math
import struct
import wave
from pathlib import Path

import httpx
import pytest

from server.tts import (
    TTSBadRequestError,
    TTSClient,
    TTSConfig,
    TTSError,
)
from server.tts.client import COMPLETED_TURNS_KEEP, MAX_STREAM_CHUNK_BYTES


def make_wav_bytes(seconds: float = 0.5, sr: int = 24000) -> bytes:
    n = int(sr * seconds)
    frames = bytearray()
    for i in range(n):
        v = int(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / sr))
        frames += int(v).to_bytes(2, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return buf.getvalue()


@pytest.fixture()
def cfg(tmp_path: Path) -> TTSConfig:
    return TTSConfig(
        base_url="http://127.0.0.1:50000",
        timeout_s=5.0,
        output_dir=tmp_path,
    )


# ----------------------------------------------------------------------
# 1. turn_id 唯一性
# ----------------------------------------------------------------------


def test_turn_id_never_repeats_within_session() -> None:
    """同一连接内连续开轮，turn_id 必须严格不同。

    原实现用 `int(time.time()*1000) % 100000` —— 毫秒对 100000 取模，
    **每 100 秒循环一次**。测试里直接模拟时间推进，验证不再碰撞。
    """
    from server.ws.server import ClientSession

    # ws 只在真正收发时用到；这里只验证 turn_id 生成，传 None 即可
    s = ClientSession(ws=None, session_id="abc123")  # type: ignore[arg-type]
    seen: set[str] = set()
    # 开 300 轮，远超原实现 100 秒周期能容纳的量
    for _ in range(300):
        tid = s.new_turn()
        assert tid not in seen, (
            f"turn_id 重复了：{tid} —— "
            "原实现用时间戳取模，长会话下会碰撞，"
            "导致 TTS 去重误拒、固件把两轮当成同一轮"
        )
        seen.add(tid)

    assert len(seen) == 300
    # 格式保持 `会话id-t序号`，兼容既有日志与监控页
    assert all(t.startswith("abc123-t") for t in seen)


def test_turn_id_is_monotonic() -> None:
    """turn_id 的序号部分必须递增（便于按日志排序排查）。"""
    from server.ws.server import ClientSession

    s = ClientSession(ws=None, session_id="s1")  # type: ignore[arg-type]
    seqs = [int(s.new_turn().rsplit("-t", 1)[1]) for _ in range(10)]
    assert seqs == sorted(seqs), f"序号不递增: {seqs}"
    assert seqs == list(range(1, 11)), f"序号应为 1..10，实际 {seqs}"


# ----------------------------------------------------------------------
# 2. 去重集合有界
# ----------------------------------------------------------------------


def test_completed_turns_is_bounded(cfg: TTSConfig) -> None:
    """`_completed_turns` 必须有上限，不能无限增长。

    原实现是无界 set，且 `reset_turn` 从未被调用 ——
    长会话下内存只增不减。
    """
    client = TTSClient(cfg)
    assert len(client._completed_turns) == 0

    # 塞入远超上限的条目
    for i in range(COMPLETED_TURNS_KEEP * 3):
        client._mark_completed(f"turn-{i}")

    assert len(client._completed_turns) <= COMPLETED_TURNS_KEEP, (
        f"去重集合增长到 {len(client._completed_turns)} 条，"
        f"超过上限 {COMPLETED_TURNS_KEEP}"
    )
    # 最近的那批必须还在（去重仍有效）
    assert f"turn-{COMPLETED_TURNS_KEEP * 3 - 1}" in client._completed_turns


def test_completed_turns_keeps_recent(cfg: TTSConfig) -> None:
    """淘汰的是最旧的，最近的必须保留 —— 否则去重失去意义。"""
    client = TTSClient(cfg)
    for i in range(COMPLETED_TURNS_KEEP + 10):
        client._mark_completed(f"t{i}")

    # 最旧的应被淘汰
    assert "t0" not in client._completed_turns
    # 最近的应保留
    assert f"t{COMPLETED_TURNS_KEEP + 9}" in client._completed_turns


# ----------------------------------------------------------------------
# 3. 422 映射为参数错误
# ----------------------------------------------------------------------


def _mock_transport(status: int, body: bytes = b"{}") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize("status", [400, 422])
def test_param_error_status_maps_to_bad_request(cfg: TTSConfig, status: int) -> None:
    """400 与 422 都必须映射成 TTSBadRequestError（不可重试）。

    zjy14 用 FastAPI + pydantic，参数校验失败返回的是 **422** 而不是 400。
    原实现只认 400，422 落进泛 TTSError，被 ws/server.py 当成
    retryable 的 TTS_FAILED —— 会白白重试，还掩盖真实原因。
    """

    async def run() -> None:
        client = TTSClient(cfg, transport=_mock_transport(status, b'{"detail":"bad"}'))
        try:
            await client.synthesize("测试", voice_id="elysia")
        finally:
            await client.aclose()

    with pytest.raises(TTSBadRequestError):
        asyncio.run(run())


def test_stream_param_error_422_maps_to_bad_request(cfg: TTSConfig) -> None:
    """流式接口的 422 同样要映射成参数错误。"""

    async def run() -> None:
        client = TTSClient(cfg, transport=_mock_transport(422, b'{"detail":"bad"}'))
        try:
            async for _ in client.synthesize_stream("测试", voice_id="elysia"):
                pass
        finally:
            await client.aclose()

    with pytest.raises(TTSBadRequestError):
        asyncio.run(run())


# ----------------------------------------------------------------------
# 4. 流式分块长度上限
# ----------------------------------------------------------------------


def test_stream_rejects_oversized_chunk(cfg: TTSConfig) -> None:
    """声明长度超过上限时必须立刻报错，而不是无界等待。

    原来没有上限校验：一旦读到损坏的长度（如 0xFFFFFFFF），
    缓冲区会一直等下去 —— 内存无界增长，直到 120s 读超时才失败，
    期间毫无提示。
    """
    bogus = struct.pack(">I", MAX_STREAM_CHUNK_BYTES + 1) + b"\x00" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=bogus)

    async def run() -> None:
        client = TTSClient(cfg, transport=httpx.MockTransport(handler))
        try:
            async for _ in client.synthesize_stream("测试", voice_id="elysia"):
                pass
        finally:
            await client.aclose()

    with pytest.raises(TTSError, match="长度异常|超过上限"):
        asyncio.run(run())


def test_stream_accepts_normal_chunk(cfg: TTSConfig) -> None:
    """正常长度的块必须照常通过（别把上限设得过严误伤正常数据）。"""
    pcm = b"\x01\x02" * 1000  # 2000 字节
    body = struct.pack(">I", len(pcm)) + pcm + struct.pack(">I", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"x-sample-rate": "24000"},
        )

    async def run() -> int:
        client = TTSClient(cfg, transport=httpx.MockTransport(handler))
        try:
            total = 0
            async for chunk in client.synthesize_stream("测试", voice_id="elysia"):
                total += len(chunk)
            return total
        finally:
            await client.aclose()

    assert asyncio.run(run()) == len(pcm)


# ----------------------------------------------------------------------
# 5. pause_profile 显式传递
# ----------------------------------------------------------------------


def test_pause_profile_defaults_to_none(cfg: TTSConfig) -> None:
    """默认必须是 none（整段合成），与 zjy13 原引擎听感一致。

    zjy14 的默认值是 natural（180ms 分段合成）。不显式传的话，
    升级引擎后韵律和音色连续性会悄悄改变 —— 用户只会觉得「声音变了」
    但找不到原因。
    """
    assert cfg.pause_profile == "none"


def test_pause_profile_sent_in_request(cfg: TTSConfig) -> None:
    """请求体里必须带上 pause_profile，不能依赖服务端默认值。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(200, content=make_wav_bytes(0.2), headers={
            "x-sample-rate": "24000",
            "x-duration-s": "0.2",
            "x-engine": "test",
        })

    async def run() -> None:
        client = TTSClient(cfg, transport=httpx.MockTransport(handler))
        try:
            await client.synthesize("测试", voice_id="elysia")
        finally:
            await client.aclose()

    asyncio.run(run())
    assert "pause_profile" in captured, (
        "请求体没有 pause_profile —— 会落到服务端默认的 natural，"
        "导致韵律与 zjy13 原引擎不一致"
    )
    assert captured["pause_profile"] == "none"


def test_pause_profile_configurable_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """可通过 TTS_PAUSE_PROFILE 改成更自然的档位。"""
    monkeypatch.setenv("TTS_PAUSE_PROFILE", "natural")
    cfg = TTSConfig.from_env()
    assert cfg.pause_profile == "natural"


# ----------------------------------------------------------------------
# 6. 整轮预算必须大于队列等待
# ----------------------------------------------------------------------


def test_turn_timeout_exceeds_queue_wait() -> None:
    """zjy13 的整轮超时必须大于 zjy14 的队列等待上限。

    两者都是 60s 时，队列一拥塞，zjy14 独自就能吃掉 zjy13 的全部预算
    （还没算 ASR/LLM/合成），结果是 zjy13 先超时、zjy14 仍在排队占槽位。
    """
    from server.ws.server import TURN_TIMEOUT_S

    # zjy14 的 queue_timeout_s 默认 60（configs/gpu.yaml）
    ZJY14_QUEUE_WAIT_S = 60.0
    assert TURN_TIMEOUT_S > ZJY14_QUEUE_WAIT_S, (
        f"整轮预算 {TURN_TIMEOUT_S}s 不大于队列等待 {ZJY14_QUEUE_WAIT_S}s —— "
        "拥塞时必然互相打架"
    )
