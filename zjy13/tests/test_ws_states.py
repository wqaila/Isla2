"""对话状态机测试。

背景：``server/ws/states.py`` 有 146 行、7 个状态、一张合法转换表，
但此前 **tests/ 里 0 个用例引用它**（连 `import` 都没有）。
状态机是 WS 链路的闸门——转换表写错会导致「处理中还能再录音」
或「出错后永久卡死」，属于必须锁住的逻辑。
"""

from __future__ import annotations

import pytest

from server.ws.states import (
    BUSY_STATES,
    DialogueStateMachine,
    IllegalTransitionError,
    State,
)


# ----------------------------------------------------------------------
# 基本状态与初始值
# ----------------------------------------------------------------------


def test_initial_state_is_idle() -> None:
    sm = DialogueStateMachine()
    assert sm.state == State.IDLE
    assert sm.is_busy is False
    assert sm.turn_id is None
    assert sm.last_error is None


def test_full_happy_path() -> None:
    """IDLE → RECORDING → ASR → LLM → TTS → PLAYING → IDLE 全链合法。"""
    sm = DialogueStateMachine()
    for target in (
        State.RECORDING,
        State.ASR_PROCESSING,
        State.LLM_PROCESSING,
        State.TTS_PROCESSING,
        State.PLAYING,
        State.IDLE,
    ):
        assert sm.to(target) == target


# ----------------------------------------------------------------------
# 非法转换必须被拒
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "frm,to",
    [
        (State.IDLE, State.PLAYING),
        (State.IDLE, State.LLM_PROCESSING),
        (State.RECORDING, State.PLAYING),
        (State.PLAYING, State.RECORDING),
        (State.TTS_PROCESSING, State.RECORDING),
    ],
)
def test_illegal_transition_raises(frm: State, to: State) -> None:
    sm = DialogueStateMachine()
    sm.reset()
    sm._state = frm  # 直接摆到起始状态，避免为了造状态而写一堆合法路径
    with pytest.raises(IllegalTransitionError) as ei:
        sm.to(to)
    assert ei.value.frm == frm
    assert ei.value.to == to
    # 非法转换不能改变当前状态
    assert sm.state == frm


def test_can_reports_transition_table() -> None:
    sm = DialogueStateMachine()
    assert sm.can(State.RECORDING) is True
    assert sm.can(State.PLAYING) is False


# ----------------------------------------------------------------------
# 半双工：busy 状态判定
# ----------------------------------------------------------------------


def test_busy_states_cover_all_processing_states() -> None:
    """处理中的 5 个状态都算 busy，IDLE / ERROR 不算。"""
    expected = {
        State.RECORDING,
        State.ASR_PROCESSING,
        State.LLM_PROCESSING,
        State.TTS_PROCESSING,
        State.PLAYING,
    }
    assert BUSY_STATES == expected
    assert State.IDLE not in BUSY_STATES
    assert State.ERROR not in BUSY_STATES


def test_is_busy_true_while_playing() -> None:
    sm = DialogueStateMachine()
    sm.to(State.RECORDING)
    assert sm.is_busy is True
    sm.to(State.ASR_PROCESSING)
    assert sm.is_busy is True
    sm.to(State.IDLE)
    assert sm.is_busy is False


# ----------------------------------------------------------------------
# 错误与恢复路径
# ----------------------------------------------------------------------


def test_fail_from_any_state() -> None:
    sm = DialogueStateMachine()
    sm.to(State.RECORDING)
    assert sm.fail("麦克风坏了") == State.ERROR
    assert sm.last_error == "麦克风坏了"


def test_error_can_recover_to_idle() -> None:
    """ERROR 必须能回 IDLE，否则会永久卡住（模块注释里的硬要求）。"""
    sm = DialogueStateMachine()
    sm.fail("炸了")
    assert sm.state == State.ERROR
    assert sm.to(State.IDLE) == State.IDLE
    assert sm.last_error is None


def test_fail_twice_keeps_latest_error() -> None:
    sm = DialogueStateMachine()
    sm.fail("第一次")
    sm.fail("第二次")
    assert sm.last_error == "第二次"


def test_reset_from_playing() -> None:
    """断连后强制复位：PLAYING 也能直接回 IDLE。"""
    sm = DialogueStateMachine()
    sm.to(State.RECORDING)
    sm.to(State.ASR_PROCESSING)
    sm.to(State.LLM_PROCESSING)
    sm.to(State.TTS_PROCESSING)
    sm.to(State.PLAYING)
    assert sm.reset() == State.IDLE
    assert sm.turn_id is None
    assert sm.last_error is None


# ----------------------------------------------------------------------
# turn_id 与快照
# ----------------------------------------------------------------------


def test_turn_id_set_and_cleared() -> None:
    sm = DialogueStateMachine()
    sm.to(State.RECORDING, turn_id="s1-t1")
    assert sm.turn_id == "s1-t1"
    sm.to(State.IDLE)
    assert sm.turn_id is None


def test_snapshot_fields() -> None:
    sm = DialogueStateMachine()
    sm.to(State.RECORDING, turn_id="s1-t7")
    snap = sm.snapshot()
    assert snap["state"] == "recording"
    assert snap["turn_id"] == "s1-t7"
    assert snap["busy"] is True
    assert snap["last_error"] is None
    assert snap["elapsed_ms"] >= 0
