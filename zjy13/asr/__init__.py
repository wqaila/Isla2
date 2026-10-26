"""本地 ASR 模块（FunASR Paraformer 中文识别）。"""

from .audio_utils import (
    AudioFormatError,
    AudioInfo,
    NormalizeResult,
    normalize_to_asr_wav,
    probe_wav,
)
from .engine import (
    ASREngine,
    ASRError,
    CUDANotAvailableError,
    DeviceInfo,
    TranscribeResult,
    check_device,
)

__all__ = [
    "ASREngine",
    "ASRError",
    "CUDANotAvailableError",
    "DeviceInfo",
    "TranscribeResult",
    "check_device",
    "AudioInfo",
    "AudioFormatError",
    "NormalizeResult",
    "probe_wav",
    "normalize_to_asr_wav",
]
