"""从角色语音素材里挑出适合 CosyVoice 零样本克隆的参考音频。

背景：CosyVoice 的 ``add_zero_shot_spk(prompt_text, prompt_wav, spk_id)``
需要**参考音频 + 逐字对应的文本**。参考音频质量直接决定克隆像不像，
所以不能随便截一段就用。

本工具做的事：
1. 解码音频（mp3/wav 都可以，用 miniaudio，不依赖 ffmpeg）
2. 按能量切出"有声段"，过滤掉静音、过短、过长、削波的
3. 用项目自带的 FunASR 把候选段转写成文本
4. 按信噪比 / 时长打分排序，输出候选清单

用法（在 .venv-asr 里跑，因为需要 numpy + miniaudio + funasr）::

    python -m tools.prepare_voice_ref scan  "C:\\path\\to\\素材.mp3"
    python -m tools.prepare_voice_ref pick  "素材.mp3" --start 160 --end 170 --out voices/elysia/ref.wav
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import wave
from contextlib import redirect_stderr
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

TARGET_SR = 24000          # CosyVoice3 原生采样率，避免额外重采样
MIN_REF_S = 3.0            # 参考音频下限：太短学不到音色
MAX_REF_S = 12.0           # 上限：太长拖慢编码，且容易掺进停顿
IDEAL_REF_S = 8.0          # 理想时长（官方示例约 3~10 秒）
CLIP_PEAK = 0.99           # 削波阈值（归一化后）
MIN_SNR_DB = 20.0          # 低于这个信噪比直接淘汰


@dataclass(slots=True)
class Segment:
    """一个候选片段。"""

    start: float
    end: float
    duration: float
    rms_db: float
    noise_db: float
    snr_db: float
    clipped_ratio: float
    score: float = 0.0
    text: str = ""


# ----------------------------------------------------------------------
# 解码
# ----------------------------------------------------------------------


def decode_audio(path: Path, sample_rate: int = TARGET_SR) -> np.ndarray:
    """解码任意音频为 float32 单声道，范围 [-1, 1]。"""
    import miniaudio

    decoded = miniaudio.decode_file(
        str(path),
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=sample_rate,
    )
    ints = np.frombuffer(bytes(decoded.samples), dtype="<i2")
    return ints.astype(np.float32) / 32768.0


# ----------------------------------------------------------------------
# 切段与打分
# ----------------------------------------------------------------------


def _frame_rms(samples: np.ndarray, sr: int, hop_s: float = 0.02) -> np.ndarray:
    hop = max(1, int(sr * hop_s))
    n = len(samples) // hop
    if n == 0:
        return np.zeros(1, dtype=np.float32)
    frames = samples[: n * hop].reshape(n, hop)
    return np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1)).astype(np.float32)


def _db(value: float) -> float:
    return 20.0 * float(np.log10(max(value, 1e-9)))


def find_segments(
    samples: np.ndarray,
    sr: int,
    *,
    min_s: float = MIN_REF_S,
    max_s: float = MAX_REF_S,
    rel_threshold: float = 0.06,
    pad_s: float = 0.15,
) -> list[Segment]:
    """按能量切出有声段。

    阈值必须**相对噪声底**而不是相对全局峰值。
    实测教训：21 分钟素材里前 20 分钟电平低、最后 1 分钟突然高 10 倍，
    若用 ``peak * rel_threshold``，阈值会被最响的那几秒抬高，
    导致前面 90% 的正常语音被判成静音，只剩 10 个候选段。
    """
    hop_s = 0.02
    rms = _frame_rms(samples, sr, hop_s)
    if rms.size == 0:
        return []

    peak = float(rms.max())
    if peak <= 0:
        return []

    # 噪声底：取最安静的 10% 帧
    noise_floor = float(np.percentile(rms, 10))

    # 阈值 = max(噪声底 * 4（约 +12dB）, 峰值 * 极小比例兜底)
    # 后者只用于防止噪声底恰好为 0（纯数字静音）时阈值退化为 0。
    thr = max(noise_floor * 4.0, peak * 0.005, 1e-5)
    voiced = rms > thr

    raw: list[tuple[float, float]] = []
    start_i: int | None = None
    for i, v in enumerate(voiced):
        if v and start_i is None:
            start_i = i
        elif not v and start_i is not None:
            raw.append((start_i * hop_s, i * hop_s))
            start_i = None
    if start_i is not None:
        raw.append((start_i * hop_s, len(voiced) * hop_s))

    # 合并间隔很短的段（句中换气），否则会被切成碎片
    merged: list[list[float]] = []
    for s, e in raw:
        if merged and s - merged[-1][1] < 0.25:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    out: list[Segment] = []
    for s, e in merged:
        s = max(0.0, s - pad_s)
        e = min(len(samples) / sr, e + pad_s)
        dur = e - s
        if dur < min_s:
            continue

        # 短段直接用；长段不能"截开头"，那样会丢掉绝大部分素材
        # （实测：21 分钟连续语音合并成少数几块，只截开头等于浪费 99%）。
        # 改为在块内滑窗，取若干个互不重叠的高能量窗口。
        if dur <= max_s:
            windows = [(s, e)]
        else:
            windows = _slide_windows(samples, sr, s, e, hop_s=IDEAL_REF_S / 2)

        for ws, we in windows:
            seg = samples[int(ws * sr) : int(we * sr)]
            if seg.size == 0:
                continue

            seg_rms = float(np.sqrt((seg.astype(np.float64) ** 2).mean()))
            snr = _db(seg_rms) - _db(noise_floor)
            clipped = float(np.mean(np.abs(seg) >= CLIP_PEAK))

            out.append(
                Segment(
                    start=round(ws, 2),
                    end=round(we, 2),
                    duration=round(we - ws, 2),
                    rms_db=round(_db(seg_rms), 1),
                    noise_db=round(_db(noise_floor), 1),
                    snr_db=round(snr, 1),
                    clipped_ratio=round(clipped, 5),
                )
            )
    return out


def _slide_windows(
    samples: np.ndarray,
    sr: int,
    start: float,
    end: float,
    *,
    hop_s: float,
    top_n: int = 8,
) -> list[tuple[float, float]]:
    """在一个长有声块里滑窗，挑出能量最稳的若干窗口。

    挑选标准用"窗口内最低的 20% 帧能量"，而不是平均值：
    平均值高的窗口可能中间夹着长停顿，参考音频里有停顿会拖累克隆效果。
    """
    hop = max(1, int(sr * 0.02))
    a = int(start * sr)
    b = int(end * sr)
    wlen = int(IDEAL_REF_S * sr)
    if b - a < wlen:
        return [(start, end)]

    step = max(1, int(sr * hop_s))
    cands: list[tuple[float, float, float]] = []
    pos = a
    while pos + wlen <= b:
        seg = samples[pos : pos + wlen]
        n = len(seg) // hop
        if n == 0:
            break
        frames = seg[: n * hop].reshape(n, hop)
        rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
        # 用低分位代表"最差的部分"，避免选到含长停顿的窗口
        floor = float(np.percentile(rms, 20))
        cands.append((pos / sr, (pos + wlen) / sr, floor))
        pos += step

    cands.sort(key=lambda c: c[2], reverse=True)

    chosen: list[tuple[float, float]] = []
    for ws, we, _ in cands:
        if len(chosen) >= top_n:
            break
        # 与已选窗口重叠超过一半就跳过，保证候选之间内容不重复
        if any(min(we, c[1]) - max(ws, c[0]) > IDEAL_REF_S / 2 for c in chosen):
            continue
        chosen.append((ws, we))
    return sorted(chosen)


def score_segments(segments: list[Segment]) -> list[Segment]:
    """打分排序：信噪比为主，时长贴近理想值为辅，削波重罚。"""
    for seg in segments:
        if seg.snr_db < MIN_SNR_DB:
            seg.score = 0.0
            continue
        # 时长得分：离 IDEAL_REF_S 越近越高，3~12 秒区间内线性衰减
        dur_pen = min(abs(seg.duration - IDEAL_REF_S) / IDEAL_REF_S, 1.0)
        dur_score = 1.0 - dur_pen
        # 信噪比得分：30dB 封顶
        snr_score = min(seg.snr_db / 30.0, 1.0)
        # 削波惩罚
        clip_pen = min(seg.clipped_ratio * 100, 1.0)
        seg.score = round(snr_score * 0.6 + dur_score * 0.4 - clip_pen, 4)
    return sorted(segments, key=lambda s: s.score, reverse=True)


# ----------------------------------------------------------------------
# 转写
# ----------------------------------------------------------------------


def transcribe_segments(
    samples: np.ndarray,
    sr: int,
    segments: list[Segment],
    *,
    model_dir: Path,
    device: str = "cuda",
) -> list[Segment]:
    """用 FunASR 给每个候选段转写文本（CosyVoice 零样本需要参考文本）。"""
    logging.disable(logging.CRITICAL)
    from funasr import AutoModel

    model = AutoModel(
        model=str(model_dir),
        device=device,
        disable_update=True,
        disable_pbar=True,
        disable_log=True,
    )
    for seg in segments:
        chunk = samples[int(seg.start * sr) : int(seg.end * sr)]
        if chunk.size == 0:
            continue
        with redirect_stderr(io.StringIO()):
            result = model.generate(input=chunk, batch_size_s=0, fs=sr)
        seg.text = str(result[0]["text"]).strip() if result else ""
    return segments


# ----------------------------------------------------------------------
# 导出
# ----------------------------------------------------------------------


def write_wav(path: Path, samples: np.ndarray, sr: int) -> None:
    """写 16-bit PCM 单声道 WAV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(samples, -1.0, 1.0)
    ints = np.round(clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(ints.tobytes())


def normalize(samples: np.ndarray, target_db: float = -3.0) -> np.ndarray:
    """峰值归一化到目标电平。

    素材里各段音量差异很大（实测同一份素材前后差 10 倍），
    统一电平能让克隆更稳定。
    """
    peak = float(np.abs(samples).max())
    if peak <= 0:
        return samples
    target = 10 ** (target_db / 20.0)
    return (samples * (target / peak)).astype(np.float32)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _default_model_dir() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / "_models" / "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"


def cmd_scan(args: argparse.Namespace) -> int:
    src = Path(args.source)
    if not src.is_file():
        print(f"[错误] 文件不存在: {src}", file=sys.stderr)
        return 2

    print(f"解码 {src.name} ...")
    samples = decode_audio(src)
    sr = TARGET_SR
    print(f"时长 {len(samples)/sr:.1f}s，采样率 {sr}Hz")

    segs = find_segments(samples, sr, min_s=args.min_s, max_s=args.max_s)
    print(f"找到 {len(segs)} 个候选段，开始转写 ...")
    segs = transcribe_segments(samples, sr, segs, model_dir=Path(args.model_dir), device=args.device)
    segs = score_segments(segs)

    kept = [s for s in segs if s.score > 0 and s.text]
    print(f"\n可用候选 {len(kept)} 个（SNR>={MIN_SNR_DB}dB 且有文本）：\n")
    print(f"{'#':>3}  {'start':>8} {'end':>8} {'dur':>6} {'snr':>6} {'score':>6}  文本")
    for i, s in enumerate(kept[: args.top], 1):
        print(
            f"{i:>3}  {s.start:>8.2f} {s.end:>8.2f} {s.duration:>6.2f} "
            f"{s.snr_db:>6.1f} {s.score:>6.3f}  {s.text}"
        )

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps([asdict(s) for s in kept], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n候选清单已写入 {out}")
    return 0


def cmd_pick(args: argparse.Namespace) -> int:
    src = Path(args.source)
    if not src.is_file():
        print(f"[错误] 文件不存在: {src}", file=sys.stderr)
        return 2

    samples = decode_audio(src)
    sr = TARGET_SR
    chunk = samples[int(args.start * sr) : int(args.end * sr)]
    if chunk.size == 0:
        print("[错误] 选中的区间为空", file=sys.stderr)
        return 2

    chunk = normalize(chunk)
    out = Path(args.out)
    write_wav(out, chunk, sr)
    dur = len(chunk) / sr
    print(f"已写入 {out}  ({dur:.2f}s @ {sr}Hz)")

    if args.text:
        text_path = out.with_suffix(".txt")
        text_path.write_text(args.text.strip(), encoding="utf-8")
        print(f"参考文本已写入 {text_path}")
        print(f"文本: {args.text.strip()}")
    else:
        print("[提示] 未提供 --text，CosyVoice 零样本克隆需要参考文本，请补上")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="挑选并导出 TTS 参考音频")
    sub = ap.add_subparsers(dest="cmd", required=True)

    scan = sub.add_parser("scan", help="扫描素材，列出高质量候选段")
    scan.add_argument("source", help="素材音频路径")
    scan.add_argument("--top", type=int, default=20, help="显示前 N 个候选")
    scan.add_argument("--min-s", type=float, default=MIN_REF_S)
    scan.add_argument("--max-s", type=float, default=MAX_REF_S)
    scan.add_argument("--device", default="cuda")
    scan.add_argument("--model-dir", default=str(_default_model_dir()))
    scan.add_argument("--json-out", help="把候选清单写入 JSON")
    scan.set_defaults(func=cmd_scan)

    pick = sub.add_parser("pick", help="按区间导出参考音频")
    pick.add_argument("source", help="素材音频路径")
    pick.add_argument("--start", type=float, required=True)
    pick.add_argument("--end", type=float, required=True)
    pick.add_argument("--out", required=True, help="输出 WAV 路径")
    pick.add_argument("--text", help="参考音频对应的文本（零样本克隆必需）")
    pick.set_defaults(func=cmd_pick)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
