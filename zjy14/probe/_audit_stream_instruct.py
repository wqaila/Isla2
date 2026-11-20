"""验证：流式路径（stream=True）是否也支持指令控制。

## 背景

项目 `synthesize_stream` 走的是 `inference_zero_shot(..., stream=True)`。
若改用 `inference_instruct2(..., stream=True)`，需要确认流式下指令是否同样生效
—— 不能只验证非流式路径。

## 判定

固定 spk_id（无）以便指令能进模型，跑 sad / happy 两条指令，
对比流式输出的首块字节与总时长。

用法：
  python _audit_stream_instruct.py
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "_audit_stream_instruct"
TEXT = "今天我们一起去看海吧，海边的风应该会很舒服。"
VOICE_WAV = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav"
MODEL_DIR = os.path.expanduser("~/models/Fun-CosyVoice3-0.5B-2512")

PROMPT_TEXT = (
    "You are a helpful assistant.<|endofprompt|>"
    "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀"
)

INSTRUCTS = [
    ("sad",   "请用非常悲伤、低沉、缓慢、想哭的语气说这句话。<|endofprompt|>"),
    ("happy", "请用非常开心、高昂、快速、兴奋的语气说这句话。<|endofprompt|>"),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("=" * 84)
    print(" 流式路径是否支持指令控制")
    print("=" * 84)

    sys.path.insert(0, os.path.expanduser("~/CosyVoice"))
    sys.path.insert(0, os.path.expanduser("~/CosyVoice/third_party/Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import CosyVoice3  # type: ignore
    import torch  # type: ignore

    print("\n  加载模型 ...")
    model = CosyVoice3(MODEL_DIR, load_vllm=False, fp16=False)

    results = {}
    for tag, instr in INSTRUCTS:
        print(f"\n  -- 流式 instruct2: {tag}")
        torch.manual_seed(1986)
        torch.cuda.manual_seed_all(1986)
        try:
            gen = model.inference_instruct2(
                TEXT, instr, VOICE_WAV, stream=True, speed=1.0
            )
            chunks = []
            first_bytes = 0
            for i, out in enumerate(gen):
                speech = out["tts_speech"]
                pcm = (speech.cpu().numpy().flatten() * 32767).astype("<i2").tobytes()
                if i == 0:
                    first_bytes = len(pcm)
                chunks.append(pcm)
            if not chunks:
                print("     [FAIL] 无输出")
                continue
            all_pcm = b"".join(chunks)
            md5 = hashlib.md5(all_pcm).hexdigest()[:12]
            dur = len(all_pcm) / 2 / 24000
            results[tag] = {
                "chunks": len(chunks), "first_bytes": first_bytes,
                "dur": round(dur, 3), "md5": md5,
            }
            (OUT / f"stream_{tag}.pcm").write_bytes(all_pcm)
            print(f"     块数={len(chunks)}  首块={first_bytes}B  "
                  f"时长={dur:.3f}s  md5={md5}")
        except Exception as exc:  # noqa: BLE001
            print(f"     [FAIL] {type(exc).__name__}: {str(exc)[:90]}")

    print()
    print("=" * 84)
    print(" 判定")
    print("=" * 84)
    if len(results) == 2:
        a, b = results["sad"], results["happy"]
        if a["md5"] == b["md5"]:
            print("\n  sad == happy → 流式下指令**不生效**")
        else:
            print(f"\n  sad != happy → 流式下指令**生效**")
            print(f"    Δ时长 = {b['dur'] - a['dur']:+.3f}s")
            print(f"    Δ首块 = {b['first_bytes'] - a['first_bytes']:+d}B")
    else:
        print("\n  数据不足")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
