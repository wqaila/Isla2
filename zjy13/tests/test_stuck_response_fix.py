"""「卡在正在回应 + 完全没声音」的回归测试（2026-11-25 全面体检）。

## 现象

用户反复反馈：板子屏幕停在「正在回应」，一点声音都没有，长按也没反应，
只能断电重启。服务端日志则表现为「音频已下发 …（等待 playback_done）」
之后什么都没有，直到心跳超时断开。

## 本次定位到的根因（按因果链排序）

1. **板子固件 `play_task` 的收尾兜底不可达**（`voice_client.c`）
   预缓冲门在 `avail < prebuf_need` 时 `continue`，而两段看门狗
   （结束标记 3 秒、断流 12 秒）全部写在该 `continue` 之后 ——
   只要音频凑不够 120ms 预缓冲且等不到结束标记，循环就永远在门口打转。
   加上「中途播空」会把 `s_prebuf_done` 复位，两个计数器永远到不了阈值，
   在彻底断流场景下是**死代码**。

2. **结束标记帧被 `turn_short` 校验连坐丢弃**（`voice_client.c`）
   结束标记（`plen == 0`）原来放在轮次校验之后。`s_turn_short` 只在
   `session_start` 时赋值，一旦它丢失或晚到，音频帧与结束标记**一起**
   被丢 → `s_playing=true`、`s_state=ST_PLAYING`、缓冲恒空，
   精确落入第 1 条。

3. **`esp_codec_dev_write` 返回值被完全丢弃**（`voice_client.c`）
   `output_opened==false` 时返回 `WRONG_STATE`、I2S 写超时时返回
   `DRV_ERR`，两种都表现为「状态在播放、缓冲在消耗、但完全没声音」，
   且没有任何日志。

4. **`finish_playback()` 首行 `if (!s_playing) return;`**（`voice_client.c`）
   卡死场景下 `s_playing` 可能已被提前清成 false，于是本函数什么都不做：
   不发 `playback_done`、不复位 `s_state`、不关功放。

5. **服务端异常路径从不补发结束标记帧**（`server/ws/server.py`）
   只有成功路径发。固件唯一的无条件兜底 `finish_playback_force()`
   的触发条件就是「收到结束标记」，因此在异常路径上永久不可达。

6. **`_on_playback_done` 在非 PLAYING 状态回 error**（`server/ws/server.py`）
   实板固件把**任何** error 当无条件复位。填充音会话期间板子回报
   `playback_done` 会被判错 → 板子 `s_playing=false` → 正文 PCM 全部
   被丢弃（板子只在 `s_playing` 为真时入队）→ 用户只听到「嗯…」然后无声。

7. **`RECORDING` 是唯一没有任何超时的 BUSY 状态**（`server/ws/server.py`）
   `recording_stop` 一丢就永久卡住，之后所有 `recording_start` 都被判
   `turn_busy`，且 RECORDING 不在可打断白名单里 —— 不可自愈。

8. **播放看门狗恒为 135 秒**（`server/ws/server.py`）
   三处都传 `PLAYBACK_MAX_GUESS_S=120`，`PLAYBACK_GRACE_S` 与
   `PLAYBACK_MIN_TIMEOUT_S` 成了死常量。

9. **心跳余量只有 10 秒**（`server/ws/server.py` vs 固件 20 秒间隔）
   丢一次心跳就必然断连。

10. **`_record_latency` 全仓库无调用点**（`server/ws/server.py`）
    导致 `_mean_latency()` 恒 None → 每轮必播填充音。

11. **zjy13 自带精简固件完全不识别结束标记帧**（`zjy13/firmware/main.c`）
    流式下 `expect_frames=0`，收尾判定恒假 → 每轮必卡。

本文件用**静态语义断言 + 行为断言**两种方式锁住这些修复。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = PROJECT_ROOT / "server" / "ws" / "server.py"
BOARD_VOICE_C = (
    PROJECT_ROOT.parent / "yuanma" / "AI-Pet-Badge-main"
    / "firmware" / "ai_pet_badge" / "main" / "voice_client.c"
)
SLIM_MAIN_C = PROJECT_ROOT / "firmware" / "main" / "main.c"


def _read(path: Path) -> str:
    assert path.is_file(), f"源码不在预期位置: {path}"
    return path.read_text(encoding="utf-8", errors="replace")


def _body(src: str, marker: str, size: int = 4000, *, nth: int = 0) -> str:
    """取某个标记之后的代码窗口，用于局部断言。

    ``nth`` 用于跳过前面的同名出现（例如函数的前向声明、
    或 ``__init__`` 里同样写法的 ``except Exception``）。
    """
    start = 0
    for _ in range(nth + 1):
        idx = src.index(marker, start)
        start = idx + 1
    return src[idx:idx + size]


def _def_body(src: str, signature: str, size: int = 4000) -> str:
    """取**函数定义**之后的代码窗口，跳过前向声明。

    前向声明是 ``static void foo(void);``（以分号结尾），
    定义是 ``static void foo(void)\\n{``。这里用后者定位。
    """
    return _body(src, signature + "\n{", size)


# ======================================================================
# 一、板子固件 voice_client.c
# ======================================================================


def test_play_task_watchdogs_run_before_prebuf_gate() -> None:
    """收尾兜底必须写在预缓冲门**之前**，否则永远不可达。

    这是「卡在正在回应且无声音」最直接的根因：
    预缓冲门的 `continue` 会把后面全部逻辑跳过。
    """
    src = _read(BOARD_VOICE_C)
    body = _def_body(src, "static void play_task(void *arg)", 6000)

    i_watchdog = body.find("s_idle_ticks++")
    # 注意：注释里也出现过 `avail < prebuf_need` 字样，
    # 所以这里用**实际代码**作为定位标记。
    i_prebuf = body.find("if (avail < prebuf_need && !s_play_draining)")
    assert i_watchdog != -1, "play_task 里找不到断流看门狗（s_idle_ticks）"
    assert i_prebuf != -1, "play_task 里找不到预缓冲门"
    assert i_watchdog < i_prebuf, (
        "断流看门狗必须排在预缓冲门之前："
        "预缓冲门的 continue 会让它永远不执行（死代码）"
    )

    i_grace = body.find("s_end_grace_ticks++")
    assert i_grace != -1, "play_task 里找不到结束标记 grace 兜底"
    assert i_grace < i_prebuf, (
        "结束标记 grace 兜底也必须排在预缓冲门之前"
    )


def test_play_task_watchdog_uses_state_not_playing() -> None:
    """兜底判据必须是 s_state（对外状态机），不能是 s_playing。

    WORKLOG-2026-11-23 第六节记录过同一个坑：卡死时 s_playing 已是 false，
    用 s_playing 判断会让兜底永不触发。
    """
    src = _read(BOARD_VOICE_C)
    body = _def_body(src, "static void play_task(void *arg)", 6000)
    assert "if (s_state == ST_PLAYING) {" in body, (
        "兜底没有以 s_state == ST_PLAYING 为判据"
    )
    # 断流兜底不能依赖 s_playing
    i = body.find("s_idle_ticks++")
    guard = body[max(0, i - 400):i]
    assert "if (s_playing &&" not in guard, (
        "断流兜底仍在用 s_playing 判断，卡死场景下不会触发"
    )


def test_play_task_reports_codec_write_failure() -> None:
    """esp_codec_dev_write 的返回值必须被检查。

    丢弃返回值时，「播放中但完全没声音」不留任何痕迹。
    """
    src = _read(BOARD_VOICE_C)
    body = _def_body(src, "static void play_task(void *arg)", 6000)
    assert "esp_codec_dev_write(s_spk, out_buf" in body
    # 必须把返回值接住并判断
    assert re.search(
        r"(const\s+)?int\s+wr\s*=\s*esp_codec_dev_write\(s_spk,\s*out_buf", body
    ), "esp_codec_dev_write 的返回值没有被接住"
    assert "s_write_err_count" in body, "缺少写失败计数"
    assert "finish_playback_force()" in body, (
        "写失败持续时没有强制收尾，会静默卡住"
    )


def test_finish_playback_early_return_considers_state() -> None:
    """finish_playback 不能只凭 s_playing 早退。

    卡死场景下 s_playing 可能已是 false 而 s_state 仍是 ST_PLAYING；
    此时 `if (!s_playing) return;` 会让它什么都不做。
    """
    src = _read(BOARD_VOICE_C)
    body = _def_body(src, "static void finish_playback(void)", 900)
    assert "if (!s_playing && s_state != ST_PLAYING) return;" in body, (
        "finish_playback 的早退条件没有考虑 s_state，"
        "卡死时会静默返回（不发 playback_done、不复位状态）"
    )


def test_end_marker_checked_before_turn_short_validation() -> None:
    """结束标记帧必须免于 turn_short 校验（且排在它之前）。

    否则 session_start 一丢，音频帧与结束标记一起被丢，
    板子彻底无法收尾。
    """
    src = _read(BOARD_VOICE_C)
    body = _def_body(
        src, "static void handle_binary(const uint8_t *data, size_t len)", 3000
    )

    i_end = body.find("if (plen == 0) {")
    i_turn = body.find("if (tshort != s_turn_short) {")
    assert i_end != -1, "找不到结束标记帧处理"
    assert i_turn != -1, "找不到轮次校验"
    assert i_end < i_turn, (
        "结束标记帧必须在轮次校验之前处理："
        "否则 session_start 丢失时结束标记会被连坐丢弃，板子永久卡住"
    )


def test_voice_client_logs_turn_mismatch() -> None:
    """丢弃非本轮帧必须留痕（前几次），便于定位 session_start 丢失。"""
    src = _read(BOARD_VOICE_C)
    assert "s_turn_mismatch_logs" in src, "轮次不匹配没有任何日志"
    body = _body(src, "if (tshort != s_turn_short) {", 600)
    assert "ESP_LOGW" in body, "轮次不匹配分支没有告警日志"


def test_playback_start_failure_marks_write_error() -> None:
    """扬声器打开失败时必须标记写失败，让兜底立刻收尾。"""
    src = _read(BOARD_VOICE_C)
    body = _def_body(
        src, "static void handle_playback_start(const char *json)", 3000
    )
    i = body.find("扬声器打开失败")
    assert i != -1, "找不到扬声器打开失败分支"
    tail = body[i:i + 400]
    assert "s_write_err_count" in tail, (
        "扬声器打开失败后没有标记写失败，"
        "会进入「状态在播放但 write 全失败」的静默无声状态"
    )


# ======================================================================
# 二、服务端 server/ws/server.py
# ======================================================================


def test_recording_watchdog_exists() -> None:
    """RECORDING 必须有超时兜底 —— 它此前是唯一没有任何超时的 BUSY 状态。"""
    src = _read(SERVER_PY)
    assert "RECORDING_MAX_S" in src, "缺少录音看门狗时长常量"
    assert "_arm_recording_timer" in src, "缺少录音看门狗装载"
    assert "_recording_watchdog" in src, "缺少录音看门狗实现"
    # 必须在 recording_start 之后装载
    body = _body(src, "async def _on_recording_start", 3000)
    assert "_arm_recording_timer" in body, (
        "进入 RECORDING 后没有装载录音看门狗"
    )
    # 正常 stop 与收尾都要撤掉
    assert "_cancel_recording_timer" in _body(
        src, "async def _on_recording_stop", 1200
    ), "recording_stop 没有撤掉录音看门狗，会误杀正常轮次"
    assert "_cancel_recording_timer" in _body(
        src, "async def _cleanup", 600
    ), "断连时没有撤掉录音看门狗"


def test_playback_done_is_soft_ignored() -> None:
    """非 PLAYING 状态的 playback_done 必须软忽略，不能回 error。

    实板固件把任何 error 当无条件复位；填充音期间回报 playback_done
    被判错会导致正文 PCM 全部被丢弃 → 只听到「嗯…」然后无声。
    """
    src = _read(SERVER_PY)
    body = _body(src, "async def _on_playback_done", 2600)
    i = body.find("if client.state.state != State.PLAYING:")
    assert i != -1, "找不到非 PLAYING 分支"
    # 取到下一个顶层语句（playback_done 收尾）为止
    end = body.find("self._cancel_playback_timer(client)", i)
    assert end != -1, "找不到正常收尾路径"
    branch = body[i:end]
    assert "send_error" not in branch, (
        "非 PLAYING 的 playback_done 仍在回 error —— "
        "实板固件会因此无条件复位，导致正文音频全部丢失"
    )
    assert "return" in branch, "非 PLAYING 分支没有直接返回"


def test_end_marker_sent_on_error_paths() -> None:
    """异常收尾路径必须补发结束标记帧。"""
    src = _read(SERVER_PY)
    # TTS 失败两处
    body = _body(src, "except TTSUnavailableError as exc:", 900)
    assert "_send_end_marker" in body, "TTSUnavailableError 路径没补发结束标记"
    body = _body(src, "except TTSError as exc:", 700)
    assert "_send_end_marker" in body, "TTSError 路径没补发结束标记"
    # 本轮超时 / 其它异常（取 _process_turn 里的那两处，
    # 跳过 __init__ 里填充音加载失败的同名写法）
    body = _body(src, "except asyncio.TimeoutError:", 700)
    assert "_send_end_marker" in body, "本轮超时路径没补发结束标记"
    # `_process_turn` 的通用兜底（跳过 __init__ 与 snapshot 里的同名写法）
    i = src.index("async def _process_turn")
    turn_body = src[i:i + 2000]
    assert "except Exception as exc:  # noqa: BLE001" in turn_body
    assert "_send_end_marker" in turn_body, "异常兜底路径没补发结束标记"
    # 播放看门狗
    body = _body(src, "async def _playback_watchdog", 2600)
    assert "_send_end_marker" in body, "播放看门狗没补发结束标记"


def test_playback_timer_rearmed_with_real_duration() -> None:
    """音频下发完必须用真实时长重装看门狗，不能停在 120 秒估计值。"""
    src = _read(SERVER_PY)
    assert "_rearm_playback_timer_after_audio" in src, (
        "缺少「按真实时长重装看门狗」的实现"
    )
    body = _body(src, "def _rearm_playback_timer_after_audio", 1600)
    assert "_arm_playback_timer" in body, (
        "重装函数没有真正调用 _arm_playback_timer"
    )
    # 调用点：必须排在「音频已下发」的日志**之前**（日志是最后一句）
    i_log = src.index("音频已下发 turn=")
    i_call = src.index("self._rearm_playback_timer_after_audio(client, my_turn")
    assert i_call < i_log, (
        "音频下发完没有重装看门狗，卡死时用户要干等 135 秒"
    )
    assert "_rearm_playback_timer_after_audio(client, my_turn, duration_s)" in src, (
        "重装看门狗没有传真实音频时长 duration_s"
    )


def test_filler_session_does_not_use_max_guess() -> None:
    """填充音会话不能用 120 秒估计值，否则也挂着 135 秒看门狗。"""
    src = _read(SERVER_PY)
    body = _body(src, "async def _start_playback_session", 1400)
    assert "0.0 if reason" in body or "PLAYBACK_MIN_TIMEOUT_S" in body, (
        "填充音会话仍在使用 PLAYBACK_MAX_GUESS_S，看门狗会长达 135 秒"
    )


def test_heartbeat_timeout_tolerates_one_missed_beat() -> None:
    """心跳超时必须能容忍固件丢一次心跳（固件间隔 20 秒）。"""
    from server.ws.server import HEARTBEAT_TIMEOUT_S

    firmware_interval_s = 20.0
    assert HEARTBEAT_TIMEOUT_S >= firmware_interval_s * 2, (
        f"心跳超时 {HEARTBEAT_TIMEOUT_S}s 不足以容忍一次丢失"
        f"（固件间隔 {firmware_interval_s}s，丢一次就是 40s）"
    )


def test_record_latency_is_actually_called() -> None:
    """_record_latency 必须真的有调用点，否则每轮都必播填充音。"""
    src = _read(SERVER_PY)
    calls = [
        m.start() for m in re.finditer(r"self\._record_latency\(", src)
    ]
    # 定义里是 `def _record_latency(self, ...)`，不算调用
    real_calls = [
        p for p in calls
        if not src[max(0, p - 6):p].endswith("def ")
    ]
    assert real_calls, (
        "_record_latency 没有任何调用点 —— "
        "_mean_latency() 恒为 None，_should_play_filler() 恒为 True，"
        "每轮必播填充音"
    )


def test_duplicate_recording_stop_is_deduplicated() -> None:
    """重复的 recording_stop 不能覆盖已有流水线（会造成双 TTS 与泄漏）。"""
    src = _read(SERVER_PY)
    body = _body(src, "async def _on_recording_stop", 2000)
    assert "client.task" in body and "done()" in body, (
        "recording_stop 没有做重复检查，第二条会覆盖 client.task，"
        "第一条流水线泄漏（双 TTS / 双 playback_start）"
    )


def test_cancel_stops_filler() -> None:
    """cancel 必须停填充音，否则残留帧会打上新轮 turn_short 混入新轮。"""
    src = _read(SERVER_PY)
    body = _body(src, "async def _on_cancel", 1200)
    assert "_stop_filler" in body, "cancel 没有停填充音"
    assert "_cancel_recording_timer" in body, "cancel 没有撤掉录音看门狗"


def test_error_state_self_heals_on_recording_start() -> None:
    """ERROR 状态下必须能自愈，否则用户按说话永远被拒。"""
    src = _read(SERVER_PY)
    body = _body(src, "async def _on_recording_start", 3600)
    assert "State.ERROR" in body, (
        "recording_start 没有处理 ERROR 状态，"
        "本轮异常后用户按说话会一直失败"
    )
    assert "reset()" in body, "ERROR 状态没有复位动作"


def test_end_marker_helper_is_idempotent() -> None:
    """结束标记帧的补发必须幂等。"""
    src = _read(SERVER_PY)
    body = _body(src, "async def _send_end_marker", 1800)
    assert "end_marker_sent" in body, "结束标记帧没有幂等保护"
    assert "sent_frames" in body, (
        "没有「本轮确实发过音频」的前置判断，会给空轮次发无关帧"
    )


# ======================================================================
# 三、zjy13 自带精简固件
# ======================================================================


def test_slim_firmware_handles_end_marker() -> None:
    """精简固件必须识别「负载长度为 0」的结束标记帧。

    它原来完全不识别：流式下 expect_frames=0，收尾判定恒假，
    每轮播放都永远不收尾。
    """
    src = _read(SLIM_MAIN_C)
    body = _def_body(
        src, "static void handle_binary_message(const uint8_t *data, size_t len)", 3000
    )
    assert "hdr.payload_len == 0" in body, (
        "精简固件不识别结束标记帧，流式下每轮都永远不收尾"
    )
    i_end = body.find("hdr.payload_len == 0")
    i_turn = body.find("hdr.turn_short != s_rt.turn_short")
    assert i_end != -1 and i_turn != -1
    assert i_end < i_turn, (
        "结束标记必须在轮次校验之前处理，否则 session_start 丢失时无法收尾"
    )


def test_slim_firmware_does_not_advance_state_without_audio() -> None:
    """没上传任何音频时不能推进到 ASR_PROCESSING（会永久 busy）。"""
    src = _read(SLIM_MAIN_C)
    body = _body(src, "ESP_LOGI(TAG, \"录音结束，共上传", 1200)
    assert "s_rt.send_seq > 0" in body, (
        "录音结束无条件推进状态：session_start 超时等路径会让状态永久 busy"
    )
    assert "app_state_reset()" in body, "无音频路径没有复位状态"


# ======================================================================
# 四、行为断言：录音看门狗与 playback_done 软忽略
# ======================================================================


def test_recording_watchdog_forces_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    """录音看门狗超时后必须把状态拉回 IDLE 并下发 turn_done。"""
    import server.ws.server as srv
    from server.ws.states import State
    from tests.test_ws_server_flow import FakeWS, _make_server

    # 直接把看门狗的 sleep 换成 0，避免测试真的等 90 秒。
    real_sleep = asyncio.sleep

    async def _fast_sleep(_delay: float, *a: object, **kw: object) -> None:
        await real_sleep(0)

    monkeypatch.setattr(srv.asyncio, "sleep", _fast_sleep)

    server, _ = _make_server()
    ws = FakeWS()
    client = srv.ClientSession(ws=ws, session_id="sess-rec-wd")
    client.state.reset()
    client.state.to(State.RECORDING)
    client.turn_id = "t-rec"

    async def scenario() -> None:
        await server._recording_watchdog(client, "t-rec")
        assert client.state.state == State.IDLE, (
            "录音看门狗没有把状态拉回 IDLE，RECORDING 会永久卡住"
        )
        reasons = [
            m.get("reason") for m in ws.json_messages()
            if m.get("type") == "turn_done"
        ]
        assert "recording_timeout" in reasons

    asyncio.run(scenario())


def test_recording_watchdog_ignores_other_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已进入新一轮时录音看门狗不得误杀。"""
    import server.ws.server as srv
    from server.ws.states import State
    from tests.test_ws_server_flow import FakeWS, _make_server

    real_sleep = asyncio.sleep

    async def _fast_sleep(_delay: float, *a: object, **kw: object) -> None:
        await real_sleep(0)

    monkeypatch.setattr(srv.asyncio, "sleep", _fast_sleep)

    server, _ = _make_server()
    client = srv.ClientSession(ws=FakeWS(), session_id="sess-rec-new")
    client.state.reset()
    client.state.to(State.RECORDING)
    client.turn_id = "t-current"

    async def scenario() -> None:
        await server._recording_watchdog(client, "t-old")
        assert client.state.state == State.RECORDING

    asyncio.run(scenario())


def test_late_playback_done_does_not_emit_error() -> None:
    """迟到的 playback_done 只能记日志，不能回 error。

    回 error 会让实板固件无条件复位 → 正文音频全丢 → 只听到填充音。
    """
    import server.ws.server as srv
    from server.ws.states import State
    from tests.test_ws_server_flow import FakeWS, _make_server

    server, _ = _make_server()
    ws = FakeWS()
    client = srv.ClientSession(ws=ws, session_id="sess-late-done")
    client.state.reset()
    client.state.to(State.RECORDING)   # 非 PLAYING
    client.turn_id = "t-late"

    async def scenario() -> None:
        await server._on_playback_done(client, {"received_bytes": 100})
        errors = [
            m for m in ws.json_messages() if m.get("type") == "error"
        ]
        assert not errors, (
            f"迟到的 playback_done 回了 error：{errors} —— "
            "实板固件会因此无条件复位，正文音频全部丢失"
        )

    asyncio.run(scenario())


def test_end_marker_helper_sends_empty_payload_frame() -> None:
    """_send_end_marker 必须真的发出一个空负载二进制帧，且只发一次。"""
    import server.ws.server as srv
    from tests.test_ws_server_flow import FakeWS, _make_server

    server, _ = _make_server()
    ws = FakeWS()
    client = srv.ClientSession(ws=ws, session_id="sess-marker")
    client.turn_id = "sess-marker-t1"
    client.sent_frames = 3      # 模拟本轮确实发过音频

    async def scenario() -> None:
        await server._send_end_marker(client)
        assert client.end_marker_sent is True
        # 第二次调用不得重复发送
        before = len(ws.sent)
        await server._send_end_marker(client)
        assert len(ws.sent) == before, "结束标记帧重复发送了"

    asyncio.run(scenario())


def test_end_marker_skipped_when_no_audio_sent() -> None:
    """本轮没发过音频时不应发结束标记帧。"""
    import server.ws.server as srv
    from tests.test_ws_server_flow import FakeWS, _make_server

    server, _ = _make_server()
    ws = FakeWS()
    client = srv.ClientSession(ws=ws, session_id="sess-noaudio")
    client.turn_id = "sess-noaudio-t1"
    client.sent_frames = 0

    async def scenario() -> None:
        await server._send_end_marker(client)
        assert not ws.sent, "没发过音频却发了结束标记帧"
        assert client.end_marker_sent is False

    asyncio.run(scenario())
