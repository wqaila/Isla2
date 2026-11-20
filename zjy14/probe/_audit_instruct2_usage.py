"""情绪控制审计：验证 inference_instruct2 是否真的能产生差异。

## 目的

前面的实验已证明：**当前项目的调用路径（inference_zero_shot）下，
emotion/instruction 字段对输出没有可辨识影响**（组间/组内 = 0.96）。

现在要回答的是：**是模型不行，还是调用路径不对？**

方法：绕过项目代码，直接调用底层 `inference_instruct2`，
对比同一文本在「无指令」与「强指令」下的输出。
如果 instruct2 能产生显著差异 → 模型支持，只是没被用上 → 只需改调用路径。
如果 instruct2 也没差异 → 模型能力上限 → 需要微调或换模型。

## 关键点：endofprompt 标记

`frontend_instruct2` 内部会把 instruct_text 当 prompt_text 用，
而模型训练时期望的指令格式带 `<|endofprompt|>` 结尾。
`cosyvoice/utils/common.py` 里的 instruct_list 全部带这个标记。
本脚本会同时测「带标记」与「不带标记」两种，验证其必要性。

用法：
  python _audit_instruct2_direct.py
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "_audit_instruct2_usage"
TEXT = "今天我们一起去看海吧，海边的风应该会很舒服。"

#: 参考音频与参考文本（与项目一致）
VOICE_WAV = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav"
MODEL_DIR = os.path.expanduser("~/models/Fun-CosyVoice3-0.5B-2512")

#: 对照：无指令 vs 强指令（带 endofprompt）
CASES = [
    ("plain_no_instruct", ""),
    ("instruct_with_marker", "请用非常悲伤、低沉、缓慢的语气说这句话。<|endofprompt|>"),
    ("instruct_no_marker", "请用非常悲伤、低沉、缓慢的语气说这句话。"),
]


def wav_data(raw: bytes) -> bytes:
    pos = 12
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        (size,) = struct.unpack("<I", raw[pos + 4:pos + 8])
        if cid == b"data":
            return raw[pos + 8:pos + 8 + size]
        pos += 8 + size
    return b""


def analyze(pcm: bytes) -> dict:
    import array
    import math

    arr = array.array("h")
    arr.frombytes(pcm)
    n = len(arr)
    if n == 0:
        return {"dur": 0}
    rms = math.sqrt(sum((v / 32768.0) ** 2 for v in arr) / n)
    zcr = sum(1 for i in range(1, n) if (arr[i] >= 0) != (arr[i - 1] >= 0)) / n

    # 自相关估 F0（取中段）
    mid = n // 2
    win = 2048
    start = max(0, mid - win // 2)
    seg = arr[start:start + win]
    f0 = None
    if len(seg) == win:
        mean = sum(seg) / win
        x = [v - mean for v in seg]
        best_lag, best = 0, -1e18
        for lag in range(48, 401):
            s = 0.0
            for i in range(win - lag):
                s += x[i] * x[i + lag]
            if s > best:
                best, best_lag = s, lag
        if best_lag:
            f0 = 24000.0 / best_lag

    return {
        "dur": round(n / 24000, 4),
        "rms": round(rms, 5),
        "zcr": round(zcr, 5),
        "f0": round(f0, 1) if f0 else None,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("=" * 92)
    print(" 直接调用 inference_instruct2 验证（绕过项目代码）")
    print("=" * 92)

    if not Path(VOICE_WAV).is_file():
        print(f"  [FAIL] 参考音频不存在: {VOICE_WAV}")
        return 1

    sys.path.insert(0, os.path.expanduser("~/CosyVoice"))
    sys.path.insert(0, os.path.expanduser("~/CosyVoice/third_party/Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import CosyVoice3  # type: ignore
    import torch  # type: ignore

    print(f"\n  加载模型: {MODEL_DIR}")
    model = CosyVoice3(MODEL_DIR, load_vllm=False, fp16=False)
    print(f"  模型类: {type(model).__name__}")
    print(f"  可用方法: {[m for m in dir(model) if m.startswith('inference_')]}")

    rows = []
    for name, instruct in CASES:
        print(f"\n  --- {name} ---")
        print(f"      instruct = {instruct!r}")
        torch.manual_seed(1986)  # 固定种子，减少采样噪声
        torch.cuda.manual_seed_all(1986)
        try:
            if instruct:
                gen = model.inference_instruct2(
                    TEXT, instruct, VOICE_WAV, stream=False, speed=1.0
                )
            else:
                gen = model.inference_zero_shot(
                    TEXT, "", VOICE_WAV, stream=False, speed=1.0
                )
            chunks = list(gen)
        except Exception as exc:  # noqa: BLE001
            print(f"      [FAIL] {type(exc).__name__}: {exc}")
            rows.append({"case": name, "error": str(exc)[:120]})
            continue

        if not chunks:
            print("      [FAIL] 无输出")
            continue

        speech = chunks[0]["tts_speech"]
        arr = (speech.cpu().numpy().flatten() * 32767).astype("<i2")
        pcm = arr.tobytes()
        a = analyze(pcm)
        md5 = hashlib.md5(pcm).hexdigest()[:12]
        (OUT / f"{name}.pcm").write_bytes(pcm)

        rows.append({"case": name, "instruct": instruct, "md5": md5, **a})
        print(f"      dur={a['dur']}s  f0={a['f0']}Hz  rms={a['rms']}  "
              f"zcr={a['zcr']}  md5={md5}")

    # ---- 汇总 ----
    print()
    print("=" * 92)
    print(" 汇总")
    print("=" * 92)
    ok = [r for r in rows if "error" not in r]
    if ok:
        print(f"\n  {'case':24s} {'dur':>8s} {'f0':>8s} {'rms':>9s} {'zcr':>8s}")
        print("  " + "-" * 62)
        for r in ok:
            print(f"  {r['case']:24s} {r['dur']:7.3f}s {str(r['f0']):>8s} "
                  f"{r['rms']:9.5f} {r['zcr']:8.5f}")

        base = next((r for r in ok if r["case"] == "plain_no_instruct"), None)
        if base:
            print(f"\n  相对无指令的差异：")
            for r in ok:
                if r["case"] == "plain_no_instruct":
                    continue
                print(f"    {r['case']:24s} "
                      f"Δdur={r['dur']-base['dur']:+.3f}s  "
                      f"Δf0={(r['f0']-base['f0']) if (r['f0'] and base['f0']) else 0:+.1f}Hz  "
                      f"Δrms={r['rms']-base['rms']:+.5f}")

    (OUT / "results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  结果已存: {OUT/'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
