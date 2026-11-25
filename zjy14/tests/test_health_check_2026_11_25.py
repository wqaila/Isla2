"""2026-11-25 体检回归：zjy14 的隐患与与 zjy13 的契约漂移。

## 背景

zjy14 是 zjy13 的引擎升级版（决策 D2），复用固件与 WebSocket 协议，
只替换 TTS 引擎。但 zjy13 在 2026-11-23 / 11-25 做了两轮大改，
zjy14 有几处没跟上；另外 zjy14 自身也有一个会冻结全服务的缺陷。

## 本文件锁住 5 类问题

1. **P0：流式路由在 async 里同步阻塞信号量**
   `threading.BoundedSemaphore.acquire(timeout=60)` 写在 `async def` 里，
   会把整个事件循环冻住最长 queue_timeout_s（默认 60 秒）。
   期间 /health、监控页、其他流全部无响应。
   同时绕过 Scheduler 的队列上限、取消检查与统计。

2. **契约漂移：lead_sec 0.8 vs zjy13 的 2.0**
   zjy13 实测 0.8 覆盖不住 TTS 分块间隔（饥饿 1.60s），改成了 2.0。

3. **契约漂移：FIRMWARE_RING_MS = 200 与实际固件不符**
   板上固件是 PREBUF_MS=120（开播门槛）+ PLAY_MAX_SECONDS=8（缓冲容量），
   原常量把两个概念混成一个，让人误判「多发就会溢出」。

4. **缺失：没有限制 token_max_hop_len**
   zjy13 实测把它从 100 降到 50，分块间隔 2.85s → 1.69s。
   zjy14 只重置了 token_hop_len，合成过程中仍会涨到 100。

5. **CSV 公式注入（CWE-1236）**
   导出 CSV 时 `text` 字段直接写出。以 `=` `+` `-` `@` 开头的文本
   在 Excel 里会被当公式执行（如 `=cmd|'/c calc'!A1`）。
"""

from __future__ import annotations

import inspect
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYNTHESIS_PY = PROJECT_ROOT / "custom_voice" / "server" / "routes" / "synthesis.py"
PROTOCOL_PY = PROJECT_ROOT / "custom_voice" / "bridge" / "protocol.py"
ENGINE_PY = PROJECT_ROOT / "custom_voice" / "inference" / "cosyvoice_engine.py"


def _read(path: Path) -> str:
    assert path.is_file(), f"源码不在预期位置: {path}"
    return path.read_text(encoding="utf-8", errors="replace")


def _code_only(src: str) -> str:
    """剥掉注释行，避免注释里的示例代码干扰断言。"""
    return "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )


# ======================================================================
# 一、P0：流式路由不得阻塞事件循环
# ======================================================================


def test_stream_route_does_not_block_event_loop() -> None:
    """流式路由不能直接调用阻塞的 semaphore.acquire。

    这是本次最严重的缺陷：`async def` 里同步等 60 秒，
    整个 FastAPI 事件循环停摆。
    """
    src = _code_only(_read(SYNTHESIS_PY))
    assert "_sem.acquire" not in src, (
        "流式路由仍在直接调用 sched._sem.acquire —— "
        "threading 信号量的阻塞等待写在 async def 里会冻结整个事件循环"
    )
    assert "acquire_slot" in src, (
        "流式路由没有使用 Scheduler.acquire_slot()"
    )
    assert "run_in_threadpool" in src, (
        "阻塞的信号量等待必须用 run_in_threadpool 挪到工作线程"
    )


def test_stream_route_releases_via_release_slot() -> None:
    """槽位必须通过 release_slot 归还（否则统计失真、取消标记泄漏）。"""
    src = _code_only(_read(SYNTHESIS_PY))
    assert "release_slot" in src, "没有用 Scheduler.release_slot() 归还槽位"
    assert "_sem.release" not in src, "仍在直接调用 sched._sem.release()"


def test_acquire_slot_is_synchronous_by_design() -> None:
    """acquire_slot 本身必须是同步函数（阻塞），由调用方负责放进线程池。

    如果它是 async 却又内部阻塞，就失去了 run_in_threadpool 的意义。
    """
    from custom_voice.inference.scheduler import Scheduler

    assert not inspect.iscoroutinefunction(Scheduler.acquire_slot), (
        "acquire_slot 不应是 async —— 它内部是阻塞的信号量 acquire，"
        "必须由调用方用 run_in_threadpool 包起来"
    )


def test_acquire_slot_checks_queue_capacity() -> None:
    """acquire_slot 必须做队列上限检查（原实现直接操作 _sem，绕过了它）。"""
    from custom_voice.inference.scheduler import QueueFullError, Scheduler

    s = Scheduler(max_concurrent=1, max_queue=1, queue_timeout_s=5)
    started = threading.Event()
    release = threading.Event()

    def hold() -> None:
        s.acquire_slot("r1")
        started.set()
        release.wait(timeout=5)
        s.release_slot("r1")

    t = threading.Thread(target=hold)
    t.start()
    assert started.wait(timeout=5)

    # 第 2 个占住队列名额（在另一个线程里等）
    t2 = threading.Thread(target=_try_acquire, args=(s, "r2"))
    t2.start()
    time.sleep(0.15)

    with pytest.raises(QueueFullError):
        s.acquire_slot("r3")

    release.set()
    t.join(timeout=5)
    t2.join(timeout=5)


def _try_acquire(s, rid: str) -> None:  # noqa: ANN001
    try:
        s.acquire_slot(rid)
        s.release_slot(rid)
    except Exception:  # noqa: BLE001
        pass


def test_acquire_slot_times_out() -> None:
    """拿不到槽位时必须抛 503 系的超时错误，而不是静默放行。"""
    from custom_voice.inference.scheduler import RequestTimeoutError, Scheduler

    s = Scheduler(max_concurrent=1, max_queue=4, queue_timeout_s=0.1)
    s.acquire_slot("hold")
    try:
        with pytest.raises(RequestTimeoutError):
            s.acquire_slot("blocked")
    finally:
        s.release_slot("hold")


def test_release_slot_updates_stats() -> None:
    """acquire/release 必须正确维护 active 与 total_served。"""
    from custom_voice.inference.scheduler import Scheduler

    s = Scheduler(max_concurrent=2, max_queue=4, queue_timeout_s=1)
    assert s.stats()["active"] == 0
    s.acquire_slot("a")
    assert s.stats()["active"] == 1
    s.release_slot("a", run_ms=12.5)
    st = s.stats()
    assert st["active"] == 0, "release 后 active 未归零"
    assert st["total_served"] == 1, "release 未计入 total_served"
    assert st["median_run_ms"] == 12.5


def test_acquire_release_keeps_semaphore_balanced() -> None:
    """反复 acquire/release 后，槽位不能泄漏（BoundedSemaphore 会报错）。"""
    from custom_voice.inference.scheduler import Scheduler

    s = Scheduler(max_concurrent=1, max_queue=4, queue_timeout_s=1)
    for i in range(20):
        s.acquire_slot(f"r{i}")
        s.release_slot(f"r{i}")
    # 若泄漏，这里会抛 ValueError（超出 BoundedSemaphore 上限）
    s.acquire_slot("final")
    s.release_slot("final")


# ======================================================================
# 二、契约漂移：与 zjy13 对齐
# ======================================================================


def test_lead_sec_matches_zjy13() -> None:
    """lead_sec 必须与 zjy13 一致（2.0），不能停留在 0.8。"""
    from custom_voice.bridge.protocol import BackpressureState

    lead = BackpressureState().lead_sec
    assert lead >= 2.0, (
        f"lead_sec={lead} 太小：zjy13 实测 0.8 覆盖不住 TTS 分块间隔"
        "（饥饿 1.60s），已改为 2.0（饥饿降到 0.47s）"
    )


def test_lead_sec_below_firmware_ring() -> None:
    """lead_sec 必须明显小于固件环形缓冲，否则会触发固件丢帧。"""
    from custom_voice.bridge.protocol import FIRMWARE_RING_MS, BackpressureState

    ring_s = FIRMWARE_RING_MS / 1000
    assert BackpressureState().lead_sec < ring_s, (
        f"lead_sec 超过固件缓冲 {ring_s}s，会触发"
        "「播放缓冲已满，丢弃采样」"
    )


def test_firmware_ring_ms_matches_real_firmware() -> None:
    """FIRMWARE_RING_MS 必须反映真实固件值（8 秒），不能是 200。"""
    from custom_voice.bridge.protocol import FIRMWARE_RING_MS

    assert FIRMWARE_RING_MS >= 5000, (
        f"FIRMWARE_RING_MS={FIRMWARE_RING_MS} 与实际固件不符："
        "voice_client.c 的 PLAY_MAX_SECONDS=8（8 秒缓冲）"
    )
    assert FIRMWARE_RING_MS == 8000, (
        "固件 PLAY_MAX_SECONDS=8，改固件时这里要同步"
    )


def test_firmware_prebuf_documented() -> None:
    """预缓冲（开播门槛）必须与缓冲容量分开表述，不能混为一谈。"""
    from custom_voice.bridge import protocol

    assert hasattr(protocol, "FIRMWARE_PREBUF_MS"), (
        "缺少 FIRMWARE_PREBUF_MS —— 预缓冲（120ms 开播门槛）"
        "与缓冲容量（8s）是两个概念，混用会误判溢出风险"
    )
    assert protocol.FIRMWARE_PREBUF_MS == 120


def test_first_burst_unchanged() -> None:
    """首块策略必须与 zjy13 的 FIRST_BURST_MS=300 一致。"""
    from custom_voice.bridge.protocol import FIRST_BURST_MS

    assert FIRST_BURST_MS == 300


def test_frame_bytes_unchanged() -> None:
    """分帧大小必须与 zjy13 的 step=4096 一致。"""
    from custom_voice.bridge.protocol import DEFAULT_FRAME_BYTES

    assert DEFAULT_FRAME_BYTES == 4096


# ======================================================================
# 三、缺失能力：限制 token_max_hop_len
# ======================================================================


def test_engine_limits_token_max_hop_len() -> None:
    """_reset_hop_len 必须同时限制 token_max_hop_len。

    zjy13 实测：max_hop=100（上游默认）分块间隔 2.85s、饥饿 1.60s；
    降到 50 后间隔 1.69s、饥饿 0.47s，RTF 仍 0.94。
    """
    src = _read(ENGINE_PY)
    assert "token_max_hop_len" in src, (
        "_reset_hop_len 没有限制 token_max_hop_len —— "
        "只重置 token_hop_len 时，合成过程中它仍会涨到 100"
    )
    assert "TOKEN_MAX_HOP_LEN" in src, "缺少 token_max_hop_len 的常量定义"


def test_token_max_hop_len_value() -> None:
    """取 50：间隔几乎减半，且 RTF 仍 <1（25 会让 RTF 升到 1.13）。"""
    from custom_voice.inference.cosyvoice_engine import TOKEN_MAX_HOP_LEN

    assert TOKEN_MAX_HOP_LEN == 50, (
        f"TOKEN_MAX_HOP_LEN={TOKEN_MAX_HOP_LEN}，"
        "zjy13 实测最优是 50（100 间隔太大，25 会 RTF>1）"
    )


def test_reset_hop_len_sets_both_attributes() -> None:
    """行为验证：_reset_hop_len 真的把两个属性都设上。"""
    from custom_voice.inference.cosyvoice_engine import (
        TOKEN_MAX_HOP_LEN,
        CosyVoiceEngine,
    )

    class _Inner:
        def __init__(self) -> None:
            self.token_hop_len = 100
            self.token_max_hop_len = 100

    inner = _Inner()

    class _Model:
        model = inner

    class _Self:
        _config = type("C", (), {"token_hop_len_reset": True})()
        _model = _Model()
        _hop_resets = 0

    CosyVoiceEngine._reset_hop_len(_Self)  # type: ignore[arg-type]
    assert inner.token_hop_len == 25, "token_hop_len 没有被重置"
    assert inner.token_max_hop_len == TOKEN_MAX_HOP_LEN, (
        "token_max_hop_len 没有被限制，合成过程中仍会涨到 100"
    )


# ======================================================================
# 四、CSV 公式注入
# ======================================================================


@pytest.mark.parametrize(
    "raw",
    ["=1+1", "+1", "-1+1", "@SUM(A1)", "\t=1+1", "\r=1+1"],
)
def test_csv_safe_escapes_formula_prefix(raw: str) -> None:
    """以危险字符开头的文本必须加前导单引号。"""
    from custom_voice.history.repository import _csv_safe

    out = _csv_safe(raw)
    assert out.startswith("'"), f"{raw!r} 未做转义，Excel 会当公式执行"
    assert out[1:] == raw, "转义不能改动原文"


@pytest.mark.parametrize("raw", ["正常文本", "", "hello", "123"])
def test_csv_safe_leaves_normal_text(raw: str) -> None:
    """普通文本不受影响。"""
    from custom_voice.history.repository import _csv_safe

    assert _csv_safe(raw) == raw


def test_csv_safe_preserves_non_strings() -> None:
    """数字/None 原样返回（负数以 - 开头但加引号会破坏类型）。"""
    from custom_voice.history.repository import _csv_safe

    assert _csv_safe(-3.5) == -3.5
    assert _csv_safe(42) == 42
    assert _csv_safe(None) is None


def test_export_applies_csv_safe() -> None:
    """两条导出路径（iter_export / export）都必须走 _csv_safe。"""
    src = _code_only(_read(PROJECT_ROOT / "custom_voice" / "history" / "repository.py"))
    assert "_csv_safe(v)" in src, "CSV 导出行没有应用 _csv_safe"
    # export() 委托 iter_export()，只需确认委托关系仍在
    assert "iter_export(f, fmt=fmt" in src or "iter_export(" in src
# ======================================================================
# 五、路径校验：符号链接绕过
# ======================================================================


def test_resolve_audio_path_rejects_symlink_escape(tmp_path: Path) -> None:
    """符号链接不得绕过扩展名校验。

    原实现只看 ``Path(raw).parts`` 里有没有 ..，而
    ``link.wav -> evil.exe`` 的 parts 里没有 ..，校验形同虚设。
    现在改用 resolve(strict=True)，对**真实目标**判断。
    """
    from custom_voice.core.errors import InvalidRequestError
    from custom_voice.server.routes.speakers import _resolve_audio_path

    real = tmp_path / "evil.exe"
    real.write_bytes(b"MZ")
    link = tmp_path / "link.wav"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不支持创建符号链接")

    with pytest.raises(InvalidRequestError):
        _resolve_audio_path(str(link))


def test_resolve_audio_path_accepts_real_wav(tmp_path: Path) -> None:
    """正常 wav 路径必须能通过。"""
    from custom_voice.server.routes.speakers import _resolve_audio_path

    good = tmp_path / "ref.wav"
    good.write_bytes(b"RIFF")
    assert _resolve_audio_path(str(good)) == good.resolve()


def test_resolve_audio_path_rejects_relative() -> None:
    """相对路径仍必须报「绝对路径」（既有契约）。"""
    from custom_voice.core.errors import InvalidRequestError
    from custom_voice.server.routes.speakers import _resolve_audio_path

    with pytest.raises(InvalidRequestError, match="绝对路径"):
        _resolve_audio_path("relative/path.wav")


# ======================================================================
# 六、数据库事务嵌套
# ======================================================================


def test_transaction_supports_nesting(tmp_path: Path) -> None:
    """嵌套 transaction 不得自锁，且内层不提前提交。"""
    from custom_voice.db.session import Database

    db = Database(tmp_path / "n.db")
    db.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    try:
        with db.transaction() as tx:
            tx.execute("INSERT INTO t (v) VALUES (?)", ("outer",))
            # 内层：不抛异常，但**不能**把 outer 提前提交
            with db.transaction() as tx2:
                tx2.execute("INSERT INTO t (v) VALUES (?)", ("inner",))
            raise RuntimeError("force rollback")
    except RuntimeError:
        pass

    # 外层回滚，两行都不该存在
    rows = db.connect().execute("SELECT COUNT(*) AS n FROM t").fetchone()
    assert rows["n"] == 0, "嵌套事务把内层提前提交了，外层回滚失效"


def test_transaction_nested_commit_works(tmp_path: Path) -> None:
    """正常嵌套提交时两行都要落库。"""
    from custom_voice.db.session import Database

    db = Database(tmp_path / "n2.db")
    db.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    with db.transaction() as tx:
        tx.execute("INSERT INTO t (v) VALUES (?)", ("outer",))
        with db.transaction() as tx2:
            tx2.execute("INSERT INTO t (v) VALUES (?)", ("inner",))

    rows = db.connect().execute("SELECT COUNT(*) AS n FROM t").fetchone()
    assert rows["n"] == 2


def test_transaction_lock_is_reentrant(tmp_path: Path) -> None:
    """嵌套时同一线程不得自锁（原文用 threading.Lock 会卡死）。"""
    from custom_voice.db.session import Database

    db = Database(tmp_path / "n3.db")
    db.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    # 若锁不可重入，这里会永久阻塞（用超时线程兜底）
    done = []

    def run() -> None:
        with db.transaction():
            with db.transaction():
                pass
        done.append(True)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=5)
    assert done, "嵌套 transaction 自锁了 —— 应使用 RLock"

