"""参考音频质量检测。

目的：在用户导入声音时，就明确告诉他"这段音频能不能用"，而不是等合成跑飞了才发现。

检测项（每项都给出可读的结论，不只是布尔值）：
1. 时长 —— CosyVoice 零样本克隆需要 3~12 秒，太长拖慢编码且掺停顿
2. 采样率/声道 —— 必须能转成 24kHz 单声道
3. 静音比例 —— 大量静音说明截取位置不对
4. 削波比例 —— 削波会让人声失真，克隆出来发闷
5. 信噪比 —— 背景噪声过大会污染音色
6. 音量电平 —— 太小声信噪比差，太大声有削波风险
7. 是否存在多人说话嫌疑 —— 用声道/能量突变粗筛（无法确定，只能提示）

设计原则：
- **不擅自修改原始文件**，只报告
- 严重问题（blocking）与警告（warning）分开，blocking 才拒绝导入
- 检测结论落库，便于事后追溯为什么某个音色效果差
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..audio.convert import TARGET_SAMPLE_RATE, probe, read_audio

# ---- 阈值（依据 CosyVoice 官方示例与 zjy13 实测经验）----

#: 参考音频最短时长（秒）。低于此值学不到音色。
MIN_DURATION_S = 3.0

#: 最长时长（秒）。超过会拖慢编码，且容易掺进停顿。
MAX_DURATION_S = 15.0

#: 理想时长（秒）
IDEAL_DURATION_S = 8.0

#: 削波阈值（归一化后绝对值）
CLIP_THRESHOLD = 0.99

#: 削波比例上限。超过则人声已失真。
MAX_CLIP_RATIO = 0.01

#: 最低信噪比（dB）
MIN_SNR_DB = 15.0

#: 理想信噪比（dB）
IDEAL_SNR_DB = 25.0

#: 估计信噪比所需的最小动态范围（dB）。
#: 低于此值说明音频几乎没有安静片段，噪声底估计不可靠。
MIN_DYNAMIC_RANGE_DB = 6.0

#: 静音比例上限
MAX_SILENCE_RATIO = 0.60

#: RMS 电平合理区间（dBFS）
MIN_RMS_DBFS = -35.0
MAX_RMS_DBFS = -6.0


@dataclass(slots=True)
class QualityIssue:
    """一条检测发现。"""

    level: str  # "blocking" | "warning" | "info"
    code: str
    message: str


@dataclass(slots=True)
class QualityReport:
    """质量检测完整报告。"""

    path: str
    ok: bool
    score: float
    duration_s: float
    sample_rate: int
    channels: int
    rms_dbfs: float
    peak: float
    snr_db: float
    silence_ratio: float
    clip_ratio: float
    issues: list[QualityIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["issues"] = [asdict(i) for i in self.issues]
        return d

    @property
    def blocking_issues(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.level == "blocking"]

    @property
    def warnings(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.level == "warning"]

    def summary(self) -> str:
        if self.ok:
            return f"可用（评分 {self.score:.2f}）"
        codes = ", ".join(i.code for i in self.blocking_issues)
        return f"不可用：{codes}"


def _db(value: float) -> float:
    return 20.0 * float(np.log10(max(value, 1e-9)))


def _frame_rms(samples: np.ndarray, sr: int, hop_s: float = 0.02) -> np.ndarray:
    """按 20ms 帧计算 RMS。"""
    hop = max(1, int(sr * hop_s))
    n = samples.size // hop
    if n == 0:
        return np.zeros(1, dtype=np.float32)
    frames = samples[: n * hop].reshape(n, hop)
    return np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1)).astype(np.float32)


def analyze(path: Path) -> QualityReport:
    """分析参考音频质量。

    无论结论如何都返回报告，不抛异常（除非文件根本无法读取）。
    """
    info = probe(path)
    samples, sr = read_audio(path)

    issues: list[QualityIssue] = []

    duration = info.duration_s
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if samples.size else 0.0
    rms_dbfs = _db(rms)

    # ---- 时长 ----
    if duration < MIN_DURATION_S:
        issues.append(
            QualityIssue(
                "blocking",
                "too_short",
                f"时长 {duration:.2f}s 低于下限 {MIN_DURATION_S}s，"
                "零样本克隆无法学到足够音色特征。请换更长的参考音频。",
            )
        )
    elif duration > MAX_DURATION_S:
        issues.append(
            QualityIssue(
                "warning",
                "too_long",
                f"时长 {duration:.2f}s 超过建议上限 {MAX_DURATION_S}s，"
                "会拖慢参考音频编码，且容易掺入停顿。建议截取 5~10 秒。",
            )
        )

    # ---- 采样率 / 声道 ----
    if info.sample_rate != TARGET_SAMPLE_RATE:
        issues.append(
            QualityIssue(
                "info",
                "will_resample",
                f"采样率 {info.sample_rate}Hz 将重采样到 {TARGET_SAMPLE_RATE}Hz。",
            )
        )
    if info.channels != 1:
        issues.append(
            QualityIssue(
                "warning",
                "will_downmix",
                f"{info.channels} 声道将降混为单声道。"
                "若多声道内容不同（如立体声分离录制），降混可能引入相位问题。",
            )
        )

    # ---- 削波 ----
    clip_ratio = float(np.mean(np.abs(samples) >= CLIP_THRESHOLD)) if samples.size else 0.0
    if clip_ratio > MAX_CLIP_RATIO:
        issues.append(
            QualityIssue(
                "blocking",
                "clipped",
                f"削波比例 {clip_ratio:.2%} 超过上限 {MAX_CLIP_RATIO:.0%}。"
                "人声已失真，克隆结果会发闷或带破音。请换未削波的素材。",
            )
        )
    elif clip_ratio > 0:
        issues.append(
            QualityIssue(
                "warning",
                "slight_clip",
                f"检测到轻微削波（{clip_ratio:.2%}）。",
            )
        )

    # ---- 静音比例 ----
    frames = _frame_rms(samples, sr)
    peak_frame = float(frames.max()) if frames.size else 0.0
    # 相对峰值 -40dB 以下视为静音
    silence_thr = max(peak_frame * 0.01, 1e-6)
    silence_ratio = float(np.mean(frames < silence_thr)) if frames.size else 1.0
    if silence_ratio > MAX_SILENCE_RATIO:
        issues.append(
            QualityIssue(
                "blocking",
                "too_much_silence",
                f"静音比例 {silence_ratio:.0%} 超过上限 {MAX_SILENCE_RATIO:.0%}。"
                "参考音频里大部分是空白，请重新截取有人声的片段。",
            )
        )
    elif silence_ratio > 0.3:
        issues.append(
            QualityIssue(
                "warning",
                "silence_notable",
                f"静音比例偏高（{silence_ratio:.0%}），建议截取更紧凑的片段。",
            )
        )

    # ---- 信噪比 ----
    # 用 10% 分位帧作为噪声底，前提是音频里确实存在"安静帧"。
    # 连续稳态音频（纯音、持续长音）没有安静帧，此时 10% 分位≈信号本身，
    # SNR 会算成 0dB —— 这是误判，必须识别出来并跳过该项检测。
    noise_floor = float(np.percentile(frames, 10)) if frames.size else 0.0
    p95 = float(np.percentile(frames, 95)) if frames.size else 0.0
    dynamic_range_db = _db(p95) - _db(noise_floor)
    snr_estimable = dynamic_range_db >= MIN_DYNAMIC_RANGE_DB

    if not snr_estimable:
        snr_db = IDEAL_SNR_DB  # 不惩罚
        issues.append(
            QualityIssue(
                "info",
                "snr_not_estimable",
                f"音频动态范围仅 {dynamic_range_db:.1f}dB，缺少安静片段，"
                "无法可靠估计信噪比，已跳过该项检测。"
                "（连续长音或已做压缩的素材会出现这种情况）",
            )
        )
    else:
        snr_db = _db(float(np.sqrt(np.mean(frames**2)))) - _db(noise_floor)
        if snr_db < MIN_SNR_DB:
            issues.append(
                QualityIssue(
                    "blocking",
                    "low_snr",
                    f"信噪比 {snr_db:.1f}dB 低于下限 {MIN_SNR_DB}dB。"
                    "背景噪声会污染音色，克隆结果会带噪声感。请换更干净的素材。",
                )
            )
        elif snr_db < IDEAL_SNR_DB:
            issues.append(
                QualityIssue(
                    "warning",
                    "snr_moderate",
                    f"信噪比 {snr_db:.1f}dB 尚可但未达理想值 {IDEAL_SNR_DB}dB。",
                )
            )

    # ---- 电平 ----
    if rms_dbfs < MIN_RMS_DBFS:
        issues.append(
            QualityIssue(
                "blocking",
                "too_quiet",
                f"音量过低（RMS {rms_dbfs:.1f} dBFS，下限 {MIN_RMS_DBFS} dBFS）。"
                "请提高录音电平或换素材。",
            )
        )
    elif rms_dbfs > MAX_RMS_DBFS:
        issues.append(
            QualityIssue(
                "warning",
                "too_loud",
                f"音量偏高（RMS {rms_dbfs:.1f} dBFS），注意是否已削波。",
            )
        )

    # ---- 多人说话嫌疑（粗筛，只能提示）----
    # 依据：单人有声段的能量包络连续；多人对话会出现明显长停顿交替。
    # 这不是可靠检测，只作为提示，不阻断。
    if frames.size > 0:
        voiced = frames >= silence_thr
        # 统计"有声-静音"切换次数，相对时长归一化
        transitions = int(np.sum(np.diff(voiced.astype(np.int8)) != 0))
        rate = transitions / max(duration, 1e-6)
        if rate > 2.0 and silence_ratio > 0.25:
            issues.append(
                QualityIssue(
                    "warning",
                    "possible_multi_speaker",
                    f"有声/静音切换频繁（{rate:.1f} 次/秒）且静音较多，"
                    "可能存在多人对话或背景音乐。此为粗略提示，请人工确认。",
                )
            )

    # ---- 评分 ----
    blocking = [i for i in issues if i.level == "blocking"]
    score = _score(
        duration=duration,
        snr_db=snr_db,
        clip_ratio=clip_ratio,
        silence_ratio=silence_ratio,
        rms_dbfs=rms_dbfs,
    )

    return QualityReport(
        path=str(path),
        ok=not blocking,
        score=score,
        duration_s=round(duration, 3),
        sample_rate=info.sample_rate,
        channels=info.channels,
        rms_dbfs=round(rms_dbfs, 1),
        peak=round(peak, 4),
        snr_db=round(snr_db, 1),
        silence_ratio=round(silence_ratio, 3),
        clip_ratio=round(clip_ratio, 5),
        issues=issues,
    )


def _score(
    *,
    duration: float,
    snr_db: float,
    clip_ratio: float,
    silence_ratio: float,
    rms_dbfs: float,
) -> float:
    """0~1 综合评分。权重依据"对克隆效果的影响程度"。"""
    # 时长：离理想值越近越高
    dur_pen = min(abs(duration - IDEAL_DURATION_S) / IDEAL_DURATION_S, 1.0)
    dur_score = 1.0 - dur_pen

    # 信噪比：25dB 封顶
    snr_score = min(max(snr_db, 0.0) / IDEAL_SNR_DB, 1.0)

    # 削波：有削波就重罚
    clip_score = max(0.0, 1.0 - clip_ratio * 100)

    # 静音：越少越好
    silence_score = max(0.0, 1.0 - silence_ratio / MAX_SILENCE_RATIO)

    # 电平：在合理区间内满分
    if MIN_RMS_DBFS <= rms_dbfs <= MAX_RMS_DBFS:
        level_score = 1.0
    else:
        dist = min(abs(rms_dbfs - MIN_RMS_DBFS), abs(rms_dbfs - MAX_RMS_DBFS))
        level_score = max(0.0, 1.0 - dist / 20.0)

    score = (
        snr_score * 0.35
        + dur_score * 0.25
        + clip_score * 0.20
        + silence_score * 0.10
        + level_score * 0.10
    )
    return round(max(0.0, min(1.0, score)), 4)


__all__ = [
    "CLIP_THRESHOLD",
    "IDEAL_DURATION_S",
    "IDEAL_SNR_DB",
    "MAX_CLIP_RATIO",
    "MAX_DURATION_S",
    "MAX_RMS_DBFS",
    "MAX_SILENCE_RATIO",
    "MIN_DURATION_S",
    "MIN_DYNAMIC_RANGE_DB",
    "MIN_RMS_DBFS",
    "MIN_SNR_DB",
    "QualityIssue",
    "QualityReport",
    "analyze",
]
