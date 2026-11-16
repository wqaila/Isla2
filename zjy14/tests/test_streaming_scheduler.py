"""Phase 4 测试：流式策略、调度器、协议解析。

覆盖：
- 长短句路由阈值
- 调度器并发/队列/超时/取消
- 流式协议的长度前缀解析（与 zjy13 一致）
"""

from __future__ import annotations

import struct
import threading
import time

import pytest

from custom_voice.inference.scheduler import (
    QueueFullError,
    RequestCancelledError,
    RequestTimeoutError,
    Scheduler,
)
from custom_voice.inference.streaming import (
    STREAM_CHAR_THRESHOLD,
    StreamPolicy,
    count_chars,
    describe_routing,
)


# ----------------------------------------------------------------------
# 字符计数与路由
# ----------------------------------------------------------------------


def test_count_chars_excludes_punctuation() -> None:
    assert count_chars("你好，世界。") == 4
    assert count_chars("abc, def!") == 6
    assert count_chars("") == 0
    assert count_chars("   \n\t ") == 0


def test_short_text_not_streamed() -> None:
    p = StreamPolicy()
    assert p.should_stream("你好呀。") is False


def test_long_text_streamed() -> None:
    p = StreamPolicy()
    text = "今天天气不错，我们下午去公园散步吧，顺便买点吃的。"
    assert count_chars(text) >= STREAM_CHAR_THRESHOLD
    assert p.should_stream(text) is True


def test_threshold_boundary() -> None:
    p = StreamPolicy(threshold=10)
    # 刚好 10 字
    assert p.should_stream("一二三四五六七八九十") is True
    # 9 字
    assert p.should_stream("一二三四五六七八九") is False


def test_stream_disabled_globally() -> None:
    p = StreamPolicy(enabled=False)
    long_text = "这是一段很长的文本" * 5
    assert p.should_stream(long_text) is False


def test_describe_routing_includes_reason() -> None:
    p = StreamPolicy()
    d = describe_routing("短句", p)
    assert d["mode"] == "oneshot"
    assert "非流式" in d["reason"]

    d2 = describe_routing("今天天气不错，我们下午去公园散步吧，顺便买点吃的。", p)
    assert d2["mode"] == "stream"
    assert "流式" in d2["reason"]


def test_policy_to_dict() -> None:
    d = StreamPolicy().to_dict()
    assert set(d) == {"enabled", "threshold", "reslice", "chunk_ms"}


# ----------------------------------------------------------------------
# 调度器
# ----------------------------------------------------------------------


def test_scheduler_runs_function() -> None:
    s = Scheduler(max_concurrent=1, max_queue=4)
    assert s.run("r1", lambda: 42) == 42


def test_scheduler_stats_after_run() -> None:
    s = Scheduler(max_concurrent=1, max_queue=4)
    s.run("r1", lambda: 1)
    s.run("r2", lambda: 2)
    st = s.stats()
    assert st["total_served"] == 2
    assert st["active"] == 0
    assert st["queued"] == 0


def test_scheduler_propagates_exception() -> None:
    s = Scheduler(max_concurrent=1, max_queue=4)

    def boom() -> None:
        raise ValueError("测试异常")

    with pytest.raises(ValueError):
        s.run("r1", boom)
    # 异常后槽位必须释放
    assert s.stats()["active"] == 0
    assert s.run("r2", lambda: "ok") == "ok"


def test_scheduler_releases_slot_on_exception() -> None:
    """异常不能让信号量泄漏，否则后续请求全部卡死。"""
    s = Scheduler(max_concurrent=1, max_queue=4)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            s.run("x", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    # 若泄漏，这里会超时
    assert s.run("final", lambda: "ok") == "ok"


def test_scheduler_queue_full() -> None:
    """队列满时立即拒绝，不无限堆积。"""
    s = Scheduler(max_concurrent=1, max_queue=1, queue_timeout_s=10)
    started = threading.Event()
    release = threading.Event()

    def slow() -> str:
        started.set()
        release.wait(timeout=5)
        return "done"

    t = threading.Thread(target=lambda: s.run("r1", slow))
    t.start()
    started.wait(timeout=5)

    # 第 2 个占满队列（在另一个线程里等）
    t2 = threading.Thread(target=lambda: _try_run(s, "r2"))
    t2.start()
    time.sleep(0.15)

    # 第 3 个应被拒绝
    with pytest.raises(QueueFullError):
        s.run("r3", lambda: "never")

    release.set()
    t.join(timeout=5)
    t2.join(timeout=5)


def _try_run(s: Scheduler, rid: str) -> None:
    try:
        s.run(rid, lambda: "ok")
    except Exception:  # noqa: BLE001
        pass


def test_scheduler_queue_timeout() -> None:
    s = Scheduler(max_concurrent=1, max_queue=4, queue_timeout_s=0.2)
    started = threading.Event()
    release = threading.Event()

    def slow() -> str:
        started.set()
        release.wait(timeout=5)
        return "done"

    t = threading.Thread(target=lambda: s.run("r1", slow))
    t.start()
    started.wait(timeout=5)

    with pytest.raises(RequestTimeoutError):
        s.run("r2", lambda: "never")

    release.set()
    t.join(timeout=5)
    assert s.stats()["total_timeout"] == 1


def test_scheduler_cancel_before_run() -> None:
    """排队期间被取消的请求应直接拒绝。"""
    s = Scheduler(max_concurrent=1, max_queue=4, queue_timeout_s=5)
    started = threading.Event()
    release = threading.Event()

    def slow() -> str:
        started.set()
        release.wait(timeout=5)
        return "done"

    t = threading.Thread(target=lambda: s.run("r1", slow))
    t.start()
    started.wait(timeout=5)

    # r2 在排队，标记取消
    s.cancel("r2")
    err: list[Exception] = []

    def try_r2() -> None:
        try:
            s.run("r2", lambda: "should not run")
        except Exception as e:  # noqa: BLE001
            err.append(e)

    t2 = threading.Thread(target=try_r2)
    t2.start()
    release.set()
    t.join(timeout=5)
    t2.join(timeout=5)

    assert any(isinstance(e, RequestCancelledError) for e in err)


def test_scheduler_is_cancelled() -> None:
    s = Scheduler()
    assert s.is_cancelled("x") is False
    s.cancel("x")
    assert s.is_cancelled("x") is True


def test_scheduler_rejects_invalid_config() -> None:
    with pytest.raises(ValueError):
        Scheduler(max_concurrent=0)
    with pytest.raises(ValueError):
        Scheduler(max_queue=-1)


def test_scheduler_stats_shape() -> None:
    s = Scheduler(max_concurrent=2, max_queue=4)
    st = s.stats()
    for k in (
        "max_concurrent",
        "max_queue",
        "active",
        "queued",
        "total_served",
        "total_rejected",
        "total_timeout",
        "total_cancelled",
        "median_wait_ms",
        "median_run_ms",
    ):
        assert k in st


# ----------------------------------------------------------------------
# 流式协议（与 zjy13 一致）
# ----------------------------------------------------------------------


def test_length_prefixed_protocol_roundtrip() -> None:
    """4 字节大端长度前缀 + 数据，末尾 0 长度结束。"""
    pcm1 = b"\x01\x02\x03\x04"
    pcm2 = b"\x05\x06"

    stream = (
        struct.pack(">I", len(pcm1)) + pcm1
        + struct.pack(">I", len(pcm2)) + pcm2
        + struct.pack(">I", 0)
    )

    # 解析
    out: list[bytes | None] = []
    pos = 0
    while pos + 4 <= len(stream):
        n = struct.unpack(">I", stream[pos:pos + 4])[0]
        pos += 4
        if n == 0:
            out.append(None)  # 结束标记
            break
        out.append(stream[pos:pos + n])
        pos += n

    assert out == [pcm1, pcm2, None]


def test_truncated_stream_has_no_end_marker() -> None:
    """断流时不应出现结束标记，调用方据此判定不完整。"""
    pcm = b"\x01\x02\x03\x04"
    # 只有数据块，缺结束标记
    stream = struct.pack(">I", len(pcm)) + pcm

    out: list[bytes | None] = []
    pos = 0
    while pos + 4 <= len(stream):
        n = struct.unpack(">I", stream[pos:pos + 4])[0]
        pos += 4
        if n == 0:
            out.append(None)
            break
        out.append(stream[pos:pos + n])
        pos += n

    assert None not in out, "截断的流不应产出结束标记"


def test_protocol_matches_zjy13() -> None:
    """协议必须与 zjy13 完全一致，否则前端/固件对不上。"""
    from pathlib import Path

    zjy13 = Path("/mnt/d/xiangmudaima/zjy13/scripts/tts_server_wsl.py")
    if not zjy13.is_file():
        pytest.skip("zjy13 仓库不在预期位置")

    src = zjy13.read_text(encoding="utf-8")
    assert 'struct.pack(">I", len(pcm))' in src
    assert 'struct.pack(">I", 0)' in src
    assert "x-sample-rate" in src
