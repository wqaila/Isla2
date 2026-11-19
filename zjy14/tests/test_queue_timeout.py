"""排队超时的真实状态码验证（走真实 FastAPI 应用）。

为什么单独一个文件：这是 2026-11-19 那个「静默失败」缺陷的核心回归。
必须确认 `QueueTimeoutError` 在**真实的 app + 真实的 StreamingResponse**
下真的变成 503，而不只是「异常类定义成 503」。

背景：
  Starlette 的 `StreamingResponse` 会**先发响应头，再迭代 body**。
  所以「在生成器内部抛异常」能否改变状态码，取决于异常发生在
  第一次 yield 之前还是之后 —— 这是个很容易想当然的地方。
"""
from __future__ import annotations

import pytest


def test_queue_timeout_maps_to_503(app) -> None:  # type: ignore[no-untyped-def]
    """排队超时必须让调用方看到 503，而不是 200 + 空音频。

    做法：把信号量全部占满 + 用 monkeypatch 把 queue_timeout_s 换成一个
    很小的值（GPUConfig 是 frozen 的，不能直接赋值），
    让流式接口必然走到排队超时分支。
    """
    from fastapi.testclient import TestClient

    from custom_voice.server.app import state_of

    with TestClient(app, raise_server_exceptions=False) as c:
        st = state_of(app)
        sched = getattr(st, "scheduler", None)
        if sched is None:
            pytest.skip("当前配置没有调度器")

        # 占满所有槽位，让下一个请求必然排队
        drained = 0
        while sched._sem.acquire(blocking=False):  # noqa: SLF001
            drained += 1
        assert drained >= 1, "信号量初始值应大于 0"

        # GPUConfig 是 frozen pydantic 模型，不能赋值。
        # 这里替换整个 config 对象为一个「只把超时改小」的副本。
        original_config = st.config
        st.config = original_config.model_copy(update={"queue_timeout_s": 0.2})
        try:
            r = c.post(
                "/synthesize_stream",
                json={"text": "排队超时回归测试，这句话要足够长以走流式路径。"},
            )
        finally:
            st.config = original_config
            for _ in range(drained):
                sched._sem.release()  # noqa: SLF001

    # 关键断言：不能是 200
    assert r.status_code != 200, (
        f"排队超时返回了 200（body={r.content[:80]!r}）—— "
        "这是静默失败：zjy13 会把「长度 0」当成正常完成，"
        "板子静默且日志无错。"
    )
    assert r.status_code == 503, (
        f"排队超时应返回 503（zjy13 对该码有专门分支），实际 {r.status_code}"
    )


def test_queue_timeout_error_is_503() -> None:
    """异常类本身的状态码映射（不依赖 app）。"""
    from custom_voice.core.errors import QueueTimeoutError

    exc = QueueTimeoutError("队列已满")
    assert exc.http_status == 503
    assert exc.code == "queue_timeout"


def _parse_len_prefixed(raw: bytes) -> tuple[list[bytes], bool]:
    """解析 4 字节大端长度前缀流。"""
    import struct

    chunks: list[bytes] = []
    pos = 0
    ended = False
    while pos + 4 <= len(raw):
        (n,) = struct.unpack(">I", raw[pos:pos + 4])
        pos += 4
        if n == 0:
            ended = True
            break
        chunks.append(raw[pos:pos + n])
        pos += n
    return chunks, ended


def test_stream_yields_audio_when_engine_returns_single_final_chunk(client) -> None:  # type: ignore[no-untyped-def]
    """引擎把全部音频放在**唯一一个** is_final=True 的块里时，流式接口必须发出去。

    2026-11-19 缺陷回归锁（P0）：

    `TTSEngine` 协议允许两种实现风格：
      A) 真流式：多个 is_final=False 音频块 + 最后 is_final=True 空块
         —— CosyVoice3 就是这样
      B) 一次性：只 yield 一个 is_final=True **且带完整 pcm** 的块
         —— StubEngine 就是这样（stub_engine.py:166-176）

    路由层原先是 `if chunk.is_final: break`，在 A 下正常，
    在 B 下会把唯一一块带音频的数据整个丢掉 ——
    流式接口返回 HTTP 200 + 零音频，板子全程无声。

    而 `configs/gpu.yaml` 的默认 engine 正是 stub，
    所以「默认配置下流式永远无声」。

    这里用默认的 stub 引擎（conftest 的 gpu_config 就是 stub）验证。
    """
    r = client.post(
        "/synthesize_stream",
        json={"text": "这是一段足够长的文本，用来触发流式路径并确认音频真的被发出去了。"},
    )
    assert r.status_code == 200
    chunks, ended = _parse_len_prefixed(r.content)
    total = sum(len(c) for c in chunks)

    assert total > 0, (
        "流式接口返回了零音频！"
        "引擎把音频放在 is_final=True 的块里时被路由层 break 掉了。"
    )
    assert ended, "缺少结束标记（长度 0）"


def test_stream_engine_header_reflects_real_engine(client) -> None:  # type: ignore[no-untyped-def]
    """x-engine 头必须反映真实引擎，不能硬编码 cosyvoice3。

    硬编码会把「引擎其实是 stub / 流式无音频」这类问题彻底掩盖 ——
    排查时看到 cosyvoice3 就以为引擎没问题，实际是 stub。
    """
    r = client.post("/synthesize_stream", json={"text": "确认响应头反映真实引擎。"})
    assert r.status_code == 200
    engine = r.headers.get("x-engine", "")
    assert engine and engine != "cosyvoice3", (
        f"测试用的是 stub 引擎，但 x-engine 头报 {engine!r} —— 说明是硬编码"
    )
    assert engine == "stub", f"x-engine 应为 stub，实际 {engine!r}"


def test_stream_writes_history_record(client) -> None:  # type: ignore[no-untyped-def]
    """流式合成必须写入 synthesis_logs。

    2026-11-19 缺陷回归锁（P1）：

    zjy13 的生产路径**只走流式**（ws/server.py 里唯一调用是
    ``synthesize_stream``）。原来只有非流式路径会写库，导致：

      - ``/api/v1/history`` 查不到任何真实请求
      - ``only_failed`` 统计恒为 0
      - 自动清理无数据可清

    线上排障时完全看不到 zjy14 侧发生过什么。
    """
    # 先取一个明显不会重名的文本
    marker = "流式落库回归验证文本"
    r = client.post("/synthesize_stream", json={"text": marker})
    assert r.status_code == 200
    # 把流读完，确保生成器跑完（落库发生在生成器尾部）
    _ = r.content

    h = client.get("/api/v1/history", params={"limit": 20})
    assert h.status_code == 200
    data = h.json()
    items = data.get("items", [])
    assert any(marker in (it.get("text") or "") for it in items), (
        "流式合成没有写入 synthesis_logs —— "
        "zjy14 的合成历史会永远是空的（因为 zjy13 只走流式）"
    )
