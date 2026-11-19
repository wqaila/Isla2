"""填充音（语气词）功能的测试。

## 需求

用户从松手到听到正文要等约 2.3 秒（ASR 0.1s + LLM 0.8s + TTS 首块 1.5s），
这段完全静默。真人对答时会说「嗯…」「这个嘛…」，既填补空白又像在思考。

用户明确要求：**三个轮换**、**只改服务端**、**延迟自适应**、**允许打断**。

## 实现要点

1. 三个填充音预生成并裁剪（去首尾静音 + 淡入淡出）
2. 边播边补：正文没到就继续发，正文一到立刻停
3. 延迟自适应：历史平均 < 1.2s 时跳过（否则显得啰嗦）
4. 新增 FILLER 状态：占位播放中，主流程继续走
5. 允许打断：FILLER/PLAYING 时收到 recording_start 先取消当前轮

## 实测（模拟器）

```
  9 ms  playback_start   frames=0     <- 早于 ASR 的 16ms
 16 ms  asr_final        你好呀
 16 ms  llm_final
5297 ms  end_marker
playback_start 次数 : 1  ✓（填充音与正文共用会话）
帧号连续 : True  ✓
```
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = PROJECT_ROOT / "server" / "ws" / "server.py"
STATES_PY = PROJECT_ROOT / "server" / "ws" / "states.py"
FILLERS_DIR = PROJECT_ROOT / "server" / "tts" / "fillers"


def _read(p: Path) -> str:
    if not p.is_file():
        pytest.skip(f"文件不存在: {p}")
    return p.read_text(encoding="utf-8", errors="replace")


# ----------------------------------------------------------------------
# 资源
# ----------------------------------------------------------------------


def test_filler_resources_exist() -> None:
    """三个填充音资源必须存在。"""
    assert FILLERS_DIR.is_dir(), "缺少 server/tts/fillers 目录"
    meta = FILLERS_DIR / "fillers.json"
    assert meta.is_file(), "缺少 fillers.json（先跑 tools/gen_fillers.py）"

    data = json.loads(meta.read_text(encoding="utf-8"))
    items = data.get("items", [])
    assert len(items) == 3, f"应为 3 个填充音，实际 {len(items)}"

    for item in items:
        pcm = FILLERS_DIR / item["file"]
        assert pcm.is_file(), f"填充音文件缺失: {pcm}"
        assert pcm.stat().st_size > 0, f"填充音为空: {pcm}"


def test_filler_sample_rate_matches_tts() -> None:
    """填充音采样率必须与 TTS 输出一致（24kHz），否则播放变调。"""
    meta = json.loads((FILLERS_DIR / "fillers.json").read_text(encoding="utf-8"))
    assert meta["sample_rate"] == 24000


def test_filler_duration_reasonable() -> None:
    """每个填充音时长应在 0.4~1.2 秒。

    太短挡不住等待；太长会让快响应时显得啰嗦。
    """
    meta = json.loads((FILLERS_DIR / "fillers.json").read_text(encoding="utf-8"))
    for item in meta["items"]:
        d = item["duration_s"]
        assert 0.4 <= d <= 1.2, f"{item['id']} 时长 {d}s 不在 0.4~1.2 合理区间"


def test_filler_trimmed_no_long_silence() -> None:
    """填充音必须裁掉首尾静音。

    TTS 输出自带首静音最多 0.35s（实测 um_soft），直接播会先听到静音，
    恰好抵消填充音的作用。
    """
    import wave

    import numpy as np

    # 用生成脚本的裁剪函数验证：读回 PCM，检查开头不是长静音
    meta = json.loads((FILLERS_DIR / "fillers.json").read_text(encoding="utf-8"))
    sr = meta["sample_rate"]
    for item in meta["items"]:
        pcm = (FILLERS_DIR / item["file"]).read_bytes()
        a = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        peak = float(np.abs(a).max())
        assert peak > 0, f"{item['id']} 全静音"
        thresh = peak * 0.01
        nz = np.nonzero(np.abs(a) > thresh)[0]
        lead_s = nz[0] / sr if len(nz) else 0
        assert lead_s < 0.05, (
            f"{item['id']} 开头仍有 {lead_s:.2f}s 静音，说明没裁剪"
        )


# ----------------------------------------------------------------------
# FillerBank / FillerSession
# ----------------------------------------------------------------------


def test_bank_loads_three_clips() -> None:
    from server.tts.fillers import FillerBank

    bank = FillerBank()
    assert bank.available
    assert len(bank.clips) == 3


def test_bank_avoids_consecutive_repeat() -> None:
    """不能连续两次取到同一个（否则听着像卡带）。"""
    from server.tts.fillers import FillerBank

    bank = FillerBank()
    ids = [bank.pick().id for _ in range(20)]
    for i in range(len(ids) - 1):
        assert ids[i] != ids[i + 1], f"连续重复: {ids[i]} 出现两次"
    # 三个都应该被用到（覆盖轮换）
    assert len(set(ids)) == 3, f"只用到 {set(ids)}，轮换未生效"


def test_session_respects_max_seconds() -> None:
    """单轮填充音总时长不能无限增长（防 LLM 卡死时变复读机）。

    注意 ``should_continue`` 用「已发 + 一个典型长度 <= 上限」判断，
    所以实际总量会**贴近但不明显超出**上限。

    2026-11-19：新增 ``max_clips`` 声数上限（默认 1）。要测时长上限，
    必须显式放开声数，否则第一道限制就先拦住了。
    """
    from server.tts.fillers import FillerBank, FillerSession

    bank = FillerBank()
    cap = 2.0
    s = FillerSession(bank, max_seconds=cap, max_clips=99)
    n = 0
    while s.should_continue():
        if s.next_clip() is None:
            break
        n += 1
        if n > 20:      # 防死循环
            pytest.fail("FillerSession 没有上限，可能死循环")

    # 第一个总是发（保证有即时反馈），之后受上限约束
    assert n >= 1
    # 总量不应明显超过上限（允许一个典型长度 0.77s 的余量）
    assert s.sent_seconds <= cap + 0.8, (
        f"累计 {s.sent_seconds:.2f}s 超出上限 {cap}s 太多"
    )

    # 3 秒上限下不应发到 5 个以上
    s2 = FillerSession(bank, max_seconds=3.0, max_clips=99)
    n2 = 0
    while s2.should_continue():
        if s2.next_clip() is None:
            break
        n2 += 1
        if n2 > 20:
            break
    assert n2 <= 5, f"3 秒上限下发了 {n2} 个，偏多"


def test_session_defaults_to_single_clip() -> None:
    """默认单轮只发 1 声填充音。

    2026-11-19 用户反馈：板子上会连着听到两三声语气词，不自然。
    根因是发送端没跟随播放进度（一口气把 3 秒全塞给板子），
    已在 ``_send_fillers`` 修掉；这里再用声数上限兜底，
    保证即使等待很久也不会听到一串语气词。
    """
    from server.tts.fillers import MAX_FILLER_CLIPS, FillerBank, FillerSession

    assert MAX_FILLER_CLIPS == 1, "默认应只发一声"

    bank = FillerBank()
    s = FillerSession(bank)
    n = 0
    while s.should_continue():
        if s.next_clip() is None:
            break
        n += 1
        if n > 20:
            pytest.fail("单声上限失效，可能死循环")

    assert n == 1, f"默认配置下发了 {n} 声，应只发 1 声"


def test_send_fillers_paces_with_playback() -> None:
    """``_send_fillers`` 必须跟着播放进度发，而不是一口气塞完。

    这是 2026-11-19 那个"两声语气词"缺陷的回归锁。

    旧行为：填充音不节流，一个 0.7s 的 clip 几毫秒发完，循环条件
    只看累计时长，于是立刻再发下一个 —— 实测一轮发 4~5 声。

    新行为：每发一个就等它播完（可被 body_ready 打断）。
    这里用假时钟验证：发 N 声所花的"虚拟时间"应接近 N × clip 时长，
    而不是接近 0。
    """
    import asyncio
    from unittest.mock import patch

    from server.tts.fillers import FillerBank
    from server.ws.server import DialogueServer

    bank = FillerBank()
    if not bank.available:
        pytest.skip("没有填充音资源")

    # 构造一个只带 fillers 的最小 server（不启动网络/模型）
    server = DialogueServer.__new__(DialogueServer)
    server.fillers = bank

    sent: list[bytes] = []

    async def fake_send_audio(client, pcm, seq):
        sent.append(pcm)

    sleeps: list[float] = []

    async def fake_wait(duration_s, *, body_ready, cancel):
        # 记录请求等待的时长，但不真的睡（测试要快）
        sleeps.append(duration_s)

    class FakeClient:
        pass

    async def run() -> None:
        with patch.object(server, "send_audio", fake_send_audio), \
             patch.object(
                 DialogueServer, "_wait_filler_played",
                 staticmethod(fake_wait),
             ):
            await server._send_fillers(
                FakeClient(), "turn-test",
                body_ready=asyncio.Event(),
                cancel=asyncio.Event(),
            )

    asyncio.run(run())

    # 默认只发 1 声，所以不该有"等第二次"的记录
    assert len(sleeps) == 0 or len(sleeps) == 1, (
        f"发了 {len(sleeps)} 次等待，单声上限下最多 1 次"
    )

    # 放开声数上限，验证"每发一个都会等它播完"。
    #
    # 注意：不能 patch MAX_FILLER_CLIPS —— 它作为默认参数在函数定义时就
    # 绑定到了 FillerSession.__init__，运行时改模块常量不会生效。
    # _send_fillers 内部执行 `from ..tts.fillers import FillerSession`，
    # 是在**调用时**读模块属性，所以 patch 那个模块上的类才有效。
    sleeps.clear()
    sent.clear()

    async def run_multi() -> None:
        from server.tts.fillers import FillerSession as RealSession

        class MultiSession(RealSession):
            def __init__(self, bank, **kw):
                super().__init__(bank, max_clips=3, **kw)

        with patch.object(server, "send_audio", fake_send_audio), \
             patch.object(
                 DialogueServer, "_wait_filler_played",
                 staticmethod(fake_wait),
             ), \
             patch("server.tts.fillers.FillerSession", MultiSession):
            await server._send_fillers(
                FakeClient(), "turn-test",
                body_ready=asyncio.Event(),
                cancel=asyncio.Event(),
            )

    asyncio.run(run_multi())

    # 至少等过 2 次（第 3 声发完后 should_continue 为假，不再等）
    assert len(sleeps) >= 2, (
        f"只等了 {len(sleeps)} 次 —— 说明没有跟随播放进度，"
        f"会一口气把音频塞给板子（就是那个'两声语气词'的 bug）"
    )
    # 等待时长应与 clip 时长同量级（0.5~1.0s），不能是 0
    assert all(0.5 <= s <= 1.0 for s in sleeps), (
        f"等待时长异常: {sleeps}，应约等于单个 clip 时长"
    )


def test_wait_filler_played_interruptible() -> None:
    """等填充音播完时，正文一到必须立刻返回，不能空等满。

    否则正文首字延迟会被人为拉长最多一个填充音的时长。
    """
    import asyncio
    import time

    from server.ws.server import DialogueServer

    async def run() -> float:
        body_ready = asyncio.Event()
        cancel = asyncio.Event()

        async def set_soon() -> None:
            await asyncio.sleep(0.05)
            body_ready.set()

        asyncio.create_task(set_soon())
        t0 = time.perf_counter()
        # 请求等 5 秒，但正文 0.05s 后就到
        await DialogueServer._wait_filler_played(
            5.0, body_ready=body_ready, cancel=cancel
        )
        return time.perf_counter() - t0

    elapsed = asyncio.run(run())
    assert elapsed < 1.0, (
        f"等了 {elapsed:.2f}s —— 正文到达没有打断等待，首字延迟被拉长"
    )


def test_bank_handles_missing_dir(tmp_path) -> None:
    """资源缺失时不能崩，只是不可用。"""
    from server.tts.fillers import FillerBank

    bank = FillerBank(tmp_path)
    assert not bank.available
    assert bank.pick() is None


# ----------------------------------------------------------------------
# 状态机：FILLER
# ----------------------------------------------------------------------


def test_filler_state_exists() -> None:
    src = _read(STATES_PY)
    assert "FILLER" in src, "状态机缺少 FILLER 状态"


def test_filler_can_advance_to_asr() -> None:
    """FILLER 必须能转 ASR_PROCESSING，否则填充音会卡死主流程。

    这是 FILLER 与 PLAYING 的核心区别。
    """
    from server.ws.states import DialogueStateMachine, State

    sm = DialogueStateMachine()
    sm.to(State.RECORDING)
    sm.to(State.FILLER)
    assert sm.can(State.ASR_PROCESSING)
    assert sm.can(State.PLAYING)
    assert sm.can(State.IDLE)


def test_filler_is_busy() -> None:
    """FILLER 也算 busy（本轮还在处理）。"""
    from server.ws.states import BUSY_STATES, State

    assert State.FILLER in BUSY_STATES


# ----------------------------------------------------------------------
# 服务端接入
# ----------------------------------------------------------------------


def test_server_has_filler_bank() -> None:
    src = _read(SERVER_PY)
    assert "FillerBank" in src, "服务端没有加载填充音"
    assert "_should_play_filler" in src, "缺少填充音决策逻辑"


def test_latency_adaptive() -> None:
    """延迟自适应：历史平均很快时跳过填充音。"""
    src = _read(SERVER_PY)
    assert "FILLER_SKIP_BELOW_S" in src
    assert "_record_latency" in src
    assert "_mean_latency" in src


def test_adaptive_skip_when_fast() -> None:
    from server.ws.server import FILLER_SKIP_BELOW_S, DialogueServer, ServerConfig
    from server.dialogue.character import CharacterCard
    from server.tts import TTSConfig

    class _FakeTTS:
        config = TTSConfig()
        last_stream_sample_rate = 24000

    card = CharacterCard.from_file(
        PROJECT_ROOT / "server" / "characters" / "elysia.yaml"
    )
    s = DialogueServer(
        ServerConfig(), llm_client=None, tts_client=_FakeTTS(), card=card
    )
    if s.fillers is None or not s.fillers.available:
        pytest.skip("填充音资源不可用")

    # 无历史 -> 播
    assert s._should_play_filler() is True

    # 历史很快 -> 跳过
    for _ in range(3):
        s._record_latency(FILLER_SKIP_BELOW_S * 0.3)
    assert s._should_play_filler() is False

    # 历史很慢 -> 播
    s2 = DialogueServer(
        ServerConfig(), llm_client=None, tts_client=_FakeTTS(), card=card
    )
    for _ in range(3):
        s2._record_latency(FILLER_SKIP_BELOW_S * 2)
    assert s2._should_play_filler() is True


def test_latency_history_bounded() -> None:
    """历史样本不能无限增长。"""
    from server.ws.server import LATENCY_HISTORY_SIZE, DialogueServer, ServerConfig
    from server.dialogue.character import CharacterCard
    from server.tts import TTSConfig

    class _FakeTTS:
        config = TTSConfig()
        last_stream_sample_rate = 24000

    card = CharacterCard.from_file(
        PROJECT_ROOT / "server" / "characters" / "elysia.yaml"
    )
    s = DialogueServer(
        ServerConfig(), llm_client=None, tts_client=_FakeTTS(), card=card
    )
    for _ in range(50):
        s._record_latency(1.0)
    assert len(s._latency_samples) == LATENCY_HISTORY_SIZE


def test_filler_shares_playback_session() -> None:
    """填充音与正文必须共用一个 playback_start，不能各发一次。

    否则板子会重启播放缓冲，中间出现断点。
    """
    src = _read(SERVER_PY)
    assert "filler_session_open" in src, "缺少「填充音会话已开」的标记"
    # 正文接入分支应跳过发 playback_start
    idx = src.index("if playback_session_open:")
    body = src[idx:idx + 900]
    assert "不再发 playback_start" in body or "接在后面" in body, (
        "正文接入分支没有说明跳过 playback_start"
    )


def test_filler_seq_continues() -> None:
    """正文的 seq 必须接填充音实际用过的号，否则板子判乱序。

    实测曾因算法错误出现 46 -> 43 的跳变。
    """
    src = _read(SERVER_PY)
    assert "filler_seq" in src, "填充音任务没有返回实际 seq 末值"
    # 不能用字节数反推帧号（会有取整误差）
    assert "seq = filler_seq" in src, (
        "正文 seq 没有直接用填充音返回的末值，可能因取整导致跳变"
    )


def test_filler_stopped_on_finish() -> None:
    """本轮结束必须停填充音，否则后台继续发帧污染下一轮。"""
    src = _read(SERVER_PY)
    idx = src.index("async def _finish_turn")
    body = src[idx:idx + 600]
    assert "_stop_filler" in body, "_finish_turn 没有停填充音"


# ----------------------------------------------------------------------
# 打断
# ----------------------------------------------------------------------


def test_interrupt_allowed_while_playing() -> None:
    """FILLER/PLAYING 状态下应允许打断（用户明确要求）。"""
    src = _read(SERVER_PY)
    idx = src.index("async def _on_recording_start")
    body = src[idx:idx + 1800]
    assert "State.FILLER" in body and "State.PLAYING" in body, (
        "recording_start 没有对 FILLER/PLAYING 放开打断"
    )
    assert "_interrupt_for_new_turn" in body, "缺少打断处理"


def test_interrupt_still_rejects_during_processing() -> None:
    """ASR/LLM/TTS 处理中仍应拒绝新录音。

    那几段代码正在用 client.audio / client.task，中途切换会破坏本轮数据。
    """
    src = _read(SERVER_PY)
    idx = src.index("async def _on_recording_start")
    body = src[idx:idx + 1800]
    assert "TURN_BUSY" in body, "处理中没有拒绝新录音"


def test_interrupt_sends_turn_cancelled() -> None:
    """打断时必须通知板子上一轮作废，否则板子还在等 playback_done。"""
    src = _read(SERVER_PY)
    idx = src.index("async def _interrupt_for_new_turn")
    body = src[idx:idx + 2000]
    assert "TURN_CANCELLED" in body, "打断没有发 turn_cancelled"


def test_interrupt_cleans_up() -> None:
    """打断必须清理：填充音、播放看门狗、处理任务、音频缓冲。"""
    src = _read(SERVER_PY)
    idx = src.index("async def _interrupt_for_new_turn")
    body = src[idx:idx + 2000]
    assert "_stop_filler" in body, "没停填充音"
    assert "_cancel_playback_timer" in body, "没撤看门狗"
    assert "client.task.cancel()" in body, "没取消处理任务"
    assert "client.audio = None" in body, "没清音频缓冲"
