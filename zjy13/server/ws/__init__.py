"""WebSocket 对话服务（服务端）。"""

from .audio import (
    AudioFormat,
    CHANNEL_MIC1,
    CHANNEL_MIC2,
    CHANNEL_REFERENCE,
    DEVICE_CHANNELS,
    TurnAudio,
    encode_pcm16_wav,
)
from .states import (
    BUSY_STATES,
    DialogueStateMachine,
    IllegalTransitionError,
    State,
)

__all__ = [
    "State",
    "BUSY_STATES",
    "IllegalTransitionError",
    "DialogueStateMachine",
    "AudioFormat",
    "TurnAudio",
    "encode_pcm16_wav",
    "DEVICE_CHANNELS",
    "CHANNEL_REFERENCE",
    "CHANNEL_MIC1",
    "CHANNEL_MIC2",
]