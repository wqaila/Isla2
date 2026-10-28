"""对话状态机。

对应 V2 第十二节：

    IDLE → RECORDING → ASR_PROCESSING → LLM_PROCESSING
         → TTS_PROCESSING → PLAYING → IDLE

设计要点：
- 明确合法转换，非法转换直接拒绝，避免状态错乱
- 异常进入 ERROR，且**必须有恢复路径**（不允许永久卡在处理中）
- 半双工控制：录音期不播放、播放期不录音（V2 第九节）
"""

from __future__ import annotations

import time
from enum import Enum


class State(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    ASR_PROCESSING = "asr_processing"
    LLM_PROCESSING = "llm_processing"
    TTS_PROCESSING = "tts_processing"
    PLAYING = "playing"
    ERROR = "error"


#: 合法转换表：当前状态 → 允许进入的状态集合
_TRANSITIONS: dict[State, frozenset[State]] = {
    State.IDLE: frozenset({State.RECORDING, State.ERROR}),
    State.RECORDING: frozenset({State.ASR_PROCESSING, State.IDLE, State.ERROR}),
    State.ASR_PROCESSING: frozenset({State.LLM_PROCESSING, State.IDLE, State.ERROR}),
    State.LLM_PROCESSING: frozenset({State.TTS_PROCESSING, State.IDLE, State.ERROR}),
    State.TTS_PROCESSING: frozenset({State.PLAYING, State.IDLE, State.ERROR}),
    State.PLAYING: frozenset({State.IDLE, State.ERROR}),
    # ERROR 必须能回到 IDLE，否则会永久卡住
    State.ERROR: frozenset({State.IDLE}),
}

#: 处理中状态：这些状态下不接受新的录音（半双工）
BUSY_STATES: frozenset[State] = frozenset({
    State.RECORDING,
    State.ASR_PROCESSING,
    State.LLM_PROCESSING,
    State.TTS_PROCESSING,
    State.PLAYING,
})


class IllegalTransitionError(RuntimeError):
    def __init__(self, frm: State, to: State) -> None:
        super().__init__(f"非法状态转换: {frm.value} → {to.value}")
        self.frm = frm
        self.to = to


class DialogueStateMachine:
    """单连接的对话状态机。"""

    def __init__(self) -> None:
        self._state = State.IDLE
        self._entered_at = time.perf_counter()
        self._last_error: str | None = None
        self._turn_id: str | None = None

    # ------------------------------------------------------------------

    @property
    def state(self) -> State:
        return self._state

    @property
    def turn_id(self) -> str | None:
        return self._turn_id

    @property
    def is_busy(self) -> bool:
        return self._state in BUSY_STATES

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._entered_at) * 1000

    @property
    def last_error(self) -> str | None:
        return self._last_error

    # ------------------------------------------------------------------

    def can(self, target: State) -> bool:
        return target in _TRANSITIONS[self._state]

    def to(self, target: State, *, turn_id: str | None = None) -> State:
        """执行状态转换。非法转换抛 IllegalTransitionError。"""
        if not self.can(target):
            raise IllegalTransitionError(self._state, target)
        self._state = target
        self._entered_at = time.perf_counter()
        if target == State.IDLE:
            self._turn_id = None
            self._last_error = None
        elif turn_id is not None:
            self._turn_id = turn_id
        return self._state

    def fail(self, message: str, *, turn_id: str | None = None) -> State:
        """进入 ERROR 状态。

        从任何状态都可进入 ERROR（含 ERROR 自身，用于记录新的错误）。
        """
        self._last_error = message
        if self._state != State.ERROR:
            self._state = State.ERROR
            self._entered_at = time.perf_counter()
        if turn_id is not None:
            self._turn_id = turn_id
        return self._state

    def reset(self) -> State:
        """强制回到 IDLE（用于连接重连或清理）。"""
        self._state = State.IDLE
        self._entered_at = time.perf_counter()
        self._turn_id = None
        self._last_error = None
        return self._state

    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "state": self._state.value,
            "turn_id": self._turn_id,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "busy": self.is_busy,
            "last_error": self._last_error,
        }


__all__ = [
    "State",
    "BUSY_STATES",
    "IllegalTransitionError",
    "DialogueStateMachine",
]