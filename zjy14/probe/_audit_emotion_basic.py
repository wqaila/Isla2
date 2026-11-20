"""情绪控制专项审计：对照实验。

## 目的

用同一参考音频、同一参考文本、同一合成文本，分别生成：
  - 无情绪指令（基线）
  - 开心/愉悦、悲伤/低落、愤怒/激动、温柔/安慰、严肃/冷静

保持其他参数一致（speed/volume/pause_profile 全部固定为默认值），
只改 emotion / instruction 字段，观察输出是否有可听差异。

## 为什么这样做

如果情绪真的进入模型，不同情绪的输出应该在**时长、基频(F0)、能量包络**
上体现差异。如果完全一致（或差异落在纯采样随机性范围内），
说明情绪没有进入推理。

## 关键点

本项目 `cosyvoice3.yaml` 内置了固定种子（1986），
所以同一文本重复合成的差异只来自模型采样，不含时间戳等外部随机源。

用法：
  python _audit_emotion_experiment.py
"""
from __future__ import annotations

import json
import hashlib
import struct
import urllib.request
import urllib.error
from pathlib import Path

BASE = "http://127.0.0.1:50001"
OUT_DIR = Path(__file__).resolve().parents[1] / "_audit_emotion_basic"

#: 固定合成文本 —— 内容中性，避免文本本身暗示情绪
TEXT = "今天我们一起去看海吧，海边的风应该会很舒服。"

#: 6 组对照：无指令 + 5 种情绪
CASES = [
    ("baseline_none",  None,  None),
    ("happy",          "happy",     "请用开心愉悦的语气说这句话。"),
    ("sad",            "sad",       "请用悲伤低落的语气说这句话。"),
    ("angry",          "angry",     "请用愤怒激动的语气说这句话。"),
    ("gentle",         "gentle",    "请用温柔安慰的语气说这句话。"),
    ("serious",        "serious",   "请用严肃冷静的语气说这句话。"),
]


def synth(text: str, **extra) -> tuple[int, dict, bytes]:
    payload = {"text": text, "voice_id": "elysia", **extra}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE}/synthesize",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def wav_stats(raw: bytes) -> dict:
    """从 WAV 字节里算时长、字节数、md5（不含 44 字节头）。"""
    if len(raw) < 44 or raw[:4] != b"RIFF":
        return {"error": "not a wav", "len": len(raw)}
    # 找 data chunk
    pos = 12
    data = b""
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        (size,) = struct.unpack("<I", raw[pos + 4:pos + 8])
        if cid == b"data":
            data = raw[pos + 8:pos + 8 + size]
            break
        pos += 8 + size
    if not data:
        return {"error": "no data chunk", "len": len(raw)}
    samples = len(data) // 2
    dur = samples / 24000
    return {
        "bytes": len(data),
        "dur_s": round(dur, 4),
        "md5": hashlib.md5(data).hexdigest()[:16],
        "data": data,
    }


def f0_and_energy(data: bytes) -> dict:
    """粗算基频中位数与能量 RMS（纯标准库，避免依赖 numpy）。"""
    import array
    import math

    arr = array.array("h")
    arr.frombytes(data)
    n = len(arr)
    if n < 2400:
        return {"f0_hz": None, "rms": None}

    # 能量
    rms = math.sqrt(sum((v / 32768.0) ** 2 for v in arr) / n)

    # 基频：取中间一段，自相关法
    mid = n // 2
    win = 2048
    seg = arr[max(0, mid - win // 2): max(0, mid - win // 2) + win]
    if len(seg) < win:
        return {"f0_hz": None, "rms": round(rms, 5)}

    mean = sum(seg) / len(seg)
    x = [v - mean for v in seg]
    best_lag, best_val = 0, -1e18
    # 人类语音基频 60~500Hz → lag 48~400 @24kHz
    for lag in range(48, 401):
        s = 0.0
        for i in range(len(x) - lag):
            s += x[i] * x[i + lag]
        if s > best_val:
            best_val, best_lag = s, lag
    f0 = 24000.0 / best_lag if best_lag else None
    return {"f0_hz": round(f0, 1) if f0 else None, "rms": round(rms, 5)}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 96)
    print(" 情绪控制专项审计：对照实验")
    print("=" * 96)
    print(f"\n  合成文本: {TEXT}")
    print(f"  固定参数: voice=elysia, speed=1.0, volume=1.0, pause_profile=none")
    print(f"  输出目录: {OUT_DIR}\n")

    rows = []
    for name, emotion, instruction in CASES:
        extra = {}
        if emotion:
            extra["emotion"] = emotion
        if instruction:
            extra["instruction"] = instruction

        st, hd, raw = synth(TEXT, **extra)
        st_info = wav_stats(raw)
        if st != 200 or "data" not in st_info:
            print(f"  {name:16s} HTTP {st}  [失败] {raw[:120]!r}")
            rows.append({"case": name, "http": st, "error": True})
            continue

        feats = f0_and_energy(st_info["data"])
        # 存文件
        fp = OUT_DIR / f"{name}.wav"
        fp.write_bytes(raw)

        rows.append({
            "case": name,
            "emotion": emotion or "-",
            "instr": (instruction or "-")[:28],
            "dur_s": st_info["dur_s"],
            "bytes": st_info["bytes"],
            "md5": st_info["md5"],
            "f0_hz": feats["f0_hz"],
            "rms": feats["rms"],
            "file": fp.name,
        })
        print(f"  {name:16s} dur={st_info['dur_s']:.3f}s  "
              f"f0={feats['f0_hz']}Hz  rms={feats['rms']}  "
              f"md5={st_info['md5']}")

    # ---- 汇总分析 ----
    print()
    print("=" * 96)
    print(" 汇总")
    print("=" * 96)
    ok = [r for r in rows if not r.get("error")]
    if len(ok) < 2:
        print("  有效样本不足，无法对比")
        return 1

    base = next((r for r in ok if r["case"] == "baseline_none"), ok[0])
    print(f"\n  基线: {base['case']}  dur={base['dur_s']}s  "
          f"f0={base['f0_hz']}  md5={base['md5']}")

    print(f"\n  {'case':16s} {'dur差':>9s} {'f0差':>8s} {'md5相同?':>10s}")
    print("  " + "-" * 50)
    identical = 0
    for r in ok:
        if r["case"] == base["case"]:
            continue
        dd = r["dur_s"] - base["dur_s"]
        df = (r["f0_hz"] - base["f0_hz"]) if (r["f0_hz"] and base["f0_hz"]) else None
        same = (r["md5"] == base["md5"])
        if same:
            identical += 1
        print(f"  {r['case']:16s} {dd:+9.3f}s "
              f"{(f'{df:+8.1f}Hz' if df is not None else '     n/a'):>10s} "
              f"{'是' if same else '否':>10s}")

    # 保存 JSON
    (OUT_DIR / "results.json").write_text(
        json.dumps({"text": TEXT, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  结果已存: {OUT_DIR/'results.json'}")

    print()
    print("=" * 96)
    if identical > 0:
        print(f"  [关键发现] {identical} 个情绪变体的输出与基线**逐字节完全相同**")
        print("             说明 emotion/instruction 字段对模型输出没有任何影响")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
