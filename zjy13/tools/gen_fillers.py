"""生成并处理填充音资源。

产物：server/tts/fillers/*.pcm（裸 PCM16LE 单声道 24kHz）
处理链：TTS 合成 → 裁首尾静音 → 淡入淡出 → 存裸 PCM

为什么裁静音：实测 TTS 输出首静音最多 0.35s，
直接播会先听到静音才听到「嗯」，恰好抵消填充音的作用。
"""

import asyncio
import io
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server.tts import TTSClient, TTSConfig

# 三个填充音（用户已确认）
FILLERS = [
    ("um", "嗯…", "通用思考，最中性"),
    ("zhege", "这个嘛…", "需要斟酌"),
    ("wu", "唔…", "有点为难"),
]

OUT_DIR = Path("server/tts/fillers")
SAMPLE_RATE = 24000

# 静音阈值：相对峰值 -40dB，避免把轻声的尾音裁掉
SILENCE_DB = -40.0
# 淡入/淡出时长
FADE_IN_MS = 10
FADE_OUT_MS = 80


def trim_silence(samples: np.ndarray, sr: int, db: float = SILENCE_DB) -> np.ndarray:
    """裁掉首尾静音，保留中间有效语音。"""
    peak = float(np.abs(samples).max())
    if peak <= 0:
        return samples
    thresh = peak * (10 ** (db / 20.0))
    nz = np.nonzero(np.abs(samples) > thresh)[0]
    if len(nz) == 0:
        return samples
    return samples[nz[0]: nz[-1] + 1]


def apply_fade(samples: np.ndarray, sr: int,
               fade_in_ms: int = FADE_IN_MS,
               fade_out_ms: int = FADE_OUT_MS) -> np.ndarray:
    """加淡入淡出，避免爆音与被切断时的突兀感。"""
    out = samples.astype(np.float32).copy()
    n_in = int(sr * fade_in_ms / 1000)
    n_out = int(sr * fade_out_ms / 1000)
    if n_in > 0 and len(out) > n_in:
        out[:n_in] *= np.linspace(0.0, 1.0, n_in, dtype=np.float32)
    if n_out > 0 and len(out) > n_out:
        out[-n_out:] *= np.linspace(1.0, 0.0, n_out, dtype=np.float32)
    return out


def to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return np.round(clipped * 32767.0).astype("<i2").tobytes()


async def main() -> int:
    cfg = TTSConfig.from_env()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    async with TTSClient(cfg) as tts:
        ok, info = await tts.health()
        if not ok:
            print("TTS 不可用:", info)
            return 1

        print(f"{'名称':<8} {'文本':<10} {'原始':>7} {'裁剪后':>7} {'静音':>7}")
        print("-" * 50)

        total = 0
        for key, text, _desc in FILLERS:
            r = await tts.synthesize(
                text, turn_id=f"filler-gen-{key}", voice_id="elysia",
                output_name=f"_tmp_filler_{key}.wav",
            )
            with wave.open(str(r.audio_path), "rb") as w:
                sr = w.getframerate()
                n = w.getnframes()
                raw = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0

            orig_dur = len(raw) / sr
            trimmed = trim_silence(raw, sr)
            faded = apply_fade(trimmed, sr)
            pcm = to_pcm16(faded)

            dst = OUT_DIR / f"filler_{key}.pcm"
            dst.write_bytes(pcm)
            total += len(pcm)

            removed = orig_dur - len(trimmed) / sr
            print(f"{key:<8} {text:<10} {orig_dur:>6.2f}s "
                  f"{len(trimmed)/sr:>6.2f}s {removed:>6.2f}s")

            # 清理临时 wav
            try:
                r.audio_path.unlink()
            except OSError:
                pass

        print("-" * 50)
        print(f"共 3 个，合计 {total} 字节（{total/2/SAMPLE_RATE:.2f}s 音频）")
        print(f"输出目录: {OUT_DIR.resolve()}")

        # 写一份元数据，便于服务端知道每个的时长
        meta = OUT_DIR / "fillers.json"
        import json
        items = []
        for key, text, desc in FILLERS:
            p = OUT_DIR / f"filler_{key}.pcm"
            n_samples = p.stat().st_size // 2
            items.append({
                "id": key,
                "text": text,
                "desc": desc,
                "file": p.name,
                "samples": n_samples,
                "duration_s": round(n_samples / SAMPLE_RATE, 3),
            })
        meta.write_text(
            json.dumps({"sample_rate": SAMPLE_RATE, "items": items},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"元数据: {meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
