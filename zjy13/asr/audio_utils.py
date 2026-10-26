"""音频格式探测与规范化。

对应方案文档第四节：
- ASR 输入统一为 16 kHz / 16-bit signed PCM / 单声道
- 开发板实际采集格式必须按硬件驱动确认，由电脑端做必要转换
- 必须记录转换前后的格式

本模块只依赖标准库 + numpy，不引入额外重采样库：
先尝试 soundfile（libsndfile），失败则回退 wave 模块。
重采样使用线性插值（第一版够用），并在结果中标注所用方法。
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

TARGET_SR = 16000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH = 2  # bytes = 16 bit

# 最短有效音频（秒）。低于此值直接拒绝，避免送空音频给模型
MIN_DURATION_S = 0.2


@dataclass(frozen=True, slots=True)
class AudioInfo:
    """音频格式描述。"""

    path: str
    sample_rate: int
    channels: int
    sample_width: int  # 字节
    frames: int

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    @property
    def bits_per_sample(self) -> int:
        return self.sample_width * 8

    def describe(self) -> str:
        return (
            f"{self.sample_rate} Hz / {self.bits_per_sample} bit / "
            f"{self.channels} ch / {self.duration_s:.2f}s"
        )

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bits_per_sample": self.bits_per_sample,
            "duration_s": round(self.duration_s, 3),
        }


class AudioFormatError(ValueError):
    """音频格式不受支持或内容无效。"""


# --------------------------------------------------------------------------
# 探测
# --------------------------------------------------------------------------


def probe_wav(path: str | Path) -> AudioInfo:
    """读取 WAV 头，返回格式信息。不加载全部采样数据。"""
    p = Path(path)
    if not p.is_file():
        raise AudioFormatError(f"文件不存在: {p}")
    if p.stat().st_size == 0:
        raise AudioFormatError(f"文件为空: {p}")

    try:
        with wave.open(str(p), "rb") as w:
            return AudioInfo(
                path=str(p),
                sample_rate=w.getframerate(),
                channels=w.getnchannels(),
                sample_width=w.getsampwidth(),
                frames=w.getnframes(),
            )
    except wave.Error as exc:
        raise AudioFormatError(
            f"不是标准 PCM WAV（{exc}）。若为压缩格式，请先转成 PCM WAV。"
        ) from exc


def needs_conversion(info: AudioInfo) -> bool:
    return (
        info.sample_rate != TARGET_SR
        or info.channels != TARGET_CHANNELS
        or info.sample_width != TARGET_SAMPLE_WIDTH
    )


# --------------------------------------------------------------------------
# 读取与转换
# --------------------------------------------------------------------------


def read_pcm(path: str | Path) -> tuple[np.ndarray, int]:
    """读取 WAV 为 float32 单声道数组，范围 [-1, 1]。返回 (samples, sample_rate)。"""
    p = Path(path)
    try:
        with wave.open(str(p), "rb") as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            width = w.getsampwidth()
            raw = w.readframes(w.getnframes())
    except wave.Error as exc:
        raise AudioFormatError(f"读取 WAV 失败: {exc}") from exc

    if not raw:
        raise AudioFormatError(f"音频没有采样数据: {p}")

    if width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        ints = (
            b[:, 0].astype(np.int32)
            | (b[:, 1].astype(np.int32) << 8)
            | (b[:, 2].astype(np.int32) << 16)
        )
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        data = ints.astype(np.float32) / 8388608.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise AudioFormatError(f"不支持的位宽: {width * 8} bit")

    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)

    return data, sr


def resample_linear(data: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """线性插值重采样。第一版够用；追求质量可换 soxr 或 librosa。"""
    if src_sr == dst_sr or data.size == 0:
        return data
    duration = data.size / src_sr
    n_out = int(round(duration * dst_sr))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.arange(data.size, dtype=np.float64)
    x_new = np.linspace(0, data.size - 1, n_out, dtype=np.float64)
    return np.interp(x_new, x_old, data).astype(np.float32)


def write_pcm16_wav(data: np.ndarray, sample_rate: int, path: str | Path) -> Path:
    """把 float32 [-1,1] 写成 16-bit PCM 单声道 WAV。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(data, -1.0, 1.0)
    ints = np.round(clipped * 32767.0).astype("<i2")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(TARGET_CHANNELS)
        w.setsampwidth(TARGET_SAMPLE_WIDTH)
        w.setframerate(sample_rate)
        w.writeframes(ints.tobytes())
    return p


# --------------------------------------------------------------------------
# 规范化入口
# --------------------------------------------------------------------------


@dataclass(slots=True)
class NormalizeResult:
    """规范化结果，包含转换前后的完整记录（文档要求）。"""

    wav_path: Path
    source: AudioInfo
    target: AudioInfo
    converted: bool
    method: str

    def to_dict(self) -> dict:
        return {
            "source": self.source.to_dict(),
            "target": self.target.to_dict(),
            "converted": self.converted,
            "method": self.method,
        }

    def summary(self) -> str:
        if not self.converted:
            return f"无需转换（{self.source.describe()}）"
        return f"已转换：{self.source.describe()} → {self.target.describe()}（{self.method}）"


def normalize_to_asr_wav(
    src: str | Path,
    out_dir: str | Path,
    *,
    name: str | None = None,
) -> NormalizeResult:
    """把任意 PCM WAV 规范化为 ASR 输入格式。

    规则（文档第四节）：
    - 采样率统一 16 kHz
    - 声道降为单声道
    - 位宽统一 16-bit

    异常（文档 3.2）：
    - 空文件 / 非 PCM WAV / 过短音频 → 抛 AudioFormatError
    """
    info = probe_wav(src)
    if info.duration_s < MIN_DURATION_S:
        raise AudioFormatError(
            f"音频过短（{info.duration_s:.3f}s < {MIN_DURATION_S}s），拒绝识别: {src}"
        )

    data, sr = read_pcm(src)
    if data.size == 0:
        raise AudioFormatError(f"音频无有效采样: {src}")

    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak < 1e-4:
        raise AudioFormatError(
            f"音频几乎静音（峰值 {peak:.6f}），可能是空录音: {src}"
        )

    converted = needs_conversion(info)
    method_parts: list[str] = []

    if sr != TARGET_SR:
        data = resample_linear(data, sr, TARGET_SR)
        method_parts.append(f"线性插值重采样 {sr}→{TARGET_SR}")
    if info.channels > 1:
        method_parts.append(f"声道 {info.channels}→1（求均值）")
    if info.sample_width != TARGET_SAMPLE_WIDTH:
        method_parts.append(f"位宽 {info.bits_per_sample}→16 bit")

    out_name = name or (Path(src).stem + ".asr16k.wav")
    out_path = write_pcm16_wav(data, TARGET_SR, Path(out_dir) / out_name)

    return NormalizeResult(
        wav_path=out_path,
        source=info,
        target=probe_wav(out_path),
        converted=converted,
        method="；".join(method_parts) if method_parts else "无",
    )


__all__ = [
    "AudioInfo",
    "AudioFormatError",
    "NormalizeResult",
    "TARGET_SR",
    "MIN_DURATION_S",
    "probe_wav",
    "read_pcm",
    "resample_linear",
    "write_pcm16_wav",
    "normalize_to_asr_wav",
    "needs_conversion",
]
