"""音频读写与格式转换。

设计原则：
- 只依赖 soundfile（WSL venv 已有），不引入 ffmpeg/miniaudio
- 所有输出统一为 CosyVoice3 原生格式：24kHz / 单声道 / 16bit
- 读入任意采样率/声道，内部转换为目标格式

与固件的一致性（关键）：
ESP32-S3 播放侧固定 24kHz（见 zjy13/firmware/main/app_audio.h），
所以输出不做二次重采样。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

#: CosyVoice3 原生采样率，也是固件播放采样率
TARGET_SAMPLE_RATE = 24000

#: 目标声道数
TARGET_CHANNELS = 1


@dataclass(frozen=True, slots=True)
class AudioInfo:
    """音频文件的基础信息。"""

    sample_rate: int
    channels: int
    duration_s: float
    frames: int
    subtype: str
    format: str

    @property
    def is_target_format(self) -> bool:
        return (
            self.sample_rate == TARGET_SAMPLE_RATE
            and self.channels == TARGET_CHANNELS
        )


def read_audio(path: Path) -> tuple[np.ndarray, int]:
    """读取音频，返回 (float32 单声道 [-1,1], 原始采样率)。

    多声道会降混为单声道（取均值）。
    """
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    return np.ascontiguousarray(mono, dtype=np.float32), int(sr)


def probe(path: Path) -> AudioInfo:
    """读取音频元信息，不加载全部样本。"""
    info = sf.info(str(path))
    return AudioInfo(
        sample_rate=info.samplerate,
        channels=info.channels,
        duration_s=float(info.duration),
        frames=int(info.frames),
        subtype=info.subtype or "",
        format=info.format or "",
    )


def resample(samples: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """重采样。

    优先用 scipy.signal.resample_poly（质量好、速度快）；
    不可用时退化为线性插值（音质略差但可用）。
    """
    if src_sr == dst_sr or samples.size == 0:
        return samples

    try:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(int(src_sr), int(dst_sr))
        up = int(dst_sr) // g
        down = int(src_sr) // g
        out = resample_poly(samples.astype(np.float64), up, down)
        return np.ascontiguousarray(out, dtype=np.float32)
    except ImportError:
        n_out = int(round(samples.size * dst_sr / src_sr))
        if n_out <= 0:
            return np.zeros(0, dtype=np.float32)
        x_old = np.linspace(0.0, 1.0, samples.size, endpoint=False)
        x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
        out = np.interp(x_new, x_old, samples)
        return np.ascontiguousarray(out, dtype=np.float32)


def to_target_format(
    samples: np.ndarray,
    src_sr: int,
    *,
    target_sr: int = TARGET_SAMPLE_RATE,
) -> np.ndarray:
    """把任意采样率的单声道音频转为目标采样率。"""
    return resample(samples, src_sr, target_sr)


def to_pcm16(samples: np.ndarray) -> bytes:
    """float32 [-1,1] -> PCM S16LE 裸字节。"""
    clipped = np.clip(samples, -1.0, 1.0)
    ints = np.round(clipped * 32767.0).astype("<i2")
    return ints.tobytes()


def write_wav(
    path: Path,
    samples: np.ndarray,
    sample_rate: int = TARGET_SAMPLE_RATE,
) -> None:
    """写出 16bit PCM WAV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(samples, -1.0, 1.0), sample_rate, subtype="PCM_16")


def normalize_peak(samples: np.ndarray, target_peak: float = 0.95) -> np.ndarray:
    """峰值归一化。全零输入原样返回，避免除零。"""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak <= 1e-9:
        return samples
    return np.ascontiguousarray(samples * (target_peak / peak), dtype=np.float32)


__all__ = [
    "TARGET_CHANNELS",
    "TARGET_SAMPLE_RATE",
    "AudioInfo",
    "normalize_peak",
    "probe",
    "read_audio",
    "resample",
    "to_pcm16",
    "to_target_format",
    "write_wav",
]
