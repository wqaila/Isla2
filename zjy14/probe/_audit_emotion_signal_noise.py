"""情绪控制审计：区分「真实情绪效应」与「采样噪声」。

## 为什么必须做这个实验

第一轮实验里，6 个变体的 md5 全不同、时长差 0~0.68s、F0 差 -167~+7Hz。
看起来"有差异"，但这**不能证明情绪生效** —— 因为：

  `cosyvoice3.yaml` 里的固定种子（1986）只在**模型加载时**设置一次，
  而 `ras_sampling`（top_p=0.8, top_k=25）在**每次推理**都会引入随机性。
  也就是说：同一文本、同一参数，连跑两次本来就会得到不同音频。

所以正确的问题是：
  **组间差异（不同情绪）是否显著大于组内差异（同情绪重复）？**

如果两者同量级 → 情绪字段对输出没有可辨识的影响，差异全是噪声。
如果组间明显更大 → 情绪确实起了作用。

## 实验设计

- 每个情绪跑 3 次（同文本、同参数）
- 计算组内离散度（时长标准差）与组间离散度
- 同时用"固定种子"对照组验证噪声量级

用法：
  python _audit_emotion_noise.py
"""
from __future__ import annotations

import hashlib
import json
import statistics
import struct
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:50001"
OUT = Path(__file__).resolve().parents[1] / "_audit_emotion_signal_noise"
TEXT = "今天我们一起去看海吧，海边的风应该会很舒服。"

CASES = [
    ("baseline", None, None),
    ("happy",    "happy",  "请用开心愉悦的语气说这句话。"),
    ("sad",      "sad",    "请用悲伤低落的语气说这句话。"),
    ("angry",    "angry",  "请用愤怒激动的语气说这句话。"),
    ("gentle",   "gentle", "请用温柔安慰的语气说这句话。"),
]
REPEAT = 3


def synth(text: str, **extra) -> bytes | None:
    payload = {"text": text, "voice_id": "elysia", **extra}
    req = urllib.request.Request(
        f"{BASE}/synthesize",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.read()
    except urllib.error.HTTPError:
        return None


def wav_data(raw: bytes) -> bytes:
    pos = 12
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        (size,) = struct.unpack("<I", raw[pos + 4:pos + 8])
        if cid == b"data":
            return raw[pos + 8:pos + 8 + size]
        pos += 8 + size
    return b""


def stats(data: bytes) -> dict:
    import array
    import math

    arr = array.array("h")
    arr.frombytes(data)
    n = len(arr)
    rms = math.sqrt(sum((v / 32768.0) ** 2 for v in arr) / n) if n else 0.0
    return {
        "dur": n / 24000,
        "rms": round(rms, 5),
        "md5": hashlib.md5(data).hexdigest()[:12],
        # 过零率：粗略反映高频/清浊比例，比 F0 稳健
        "zcr": round(sum(1 for i in range(1, n) if (arr[i] >= 0) != (arr[i - 1] >= 0)) / n, 5),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("=" * 92)
    print(" 情绪控制审计：组间差异 vs 组内（采样）噪声")
    print("=" * 92)
    print(f"\n  文本: {TEXT}")
    print(f"  每个情绪重复 {REPEAT} 次，共 {len(CASES)*REPEAT} 次合成\n")

    groups: dict[str, list[dict]] = {}
    for name, emotion, instr in CASES:
        extra = {}
        if emotion:
            extra["emotion"] = emotion
        if instr:
            extra["instruction"] = instr
        rows = []
        for i in range(REPEAT):
            raw = synth(TEXT, **extra)
            if raw is None:
                print(f"  {name} #{i+1} 失败")
                continue
            d = wav_data(raw)
            s = stats(d)
            s["run"] = i + 1
            rows.append(s)
            (OUT / f"{name}_{i+1}.wav").write_bytes(raw)
            print(f"  {name:10s} #{i+1}  dur={s['dur']:.3f}s  "
                  f"rms={s['rms']}  zcr={s['zcr']}  md5={s['md5']}")
        groups[name] = rows
        print()

    # ---- 组内离散度 ----
    print("=" * 92)
    print(" 组内离散度（同一情绪重复跑 → 纯采样噪声）")
    print("=" * 92)
    within_dur, within_rms, within_zcr = [], [], []
    for name, rows in groups.items():
        if len(rows) < 2:
            continue
        durs = [r["dur"] for r in rows]
        rmss = [r["rms"] for r in rows]
        zcrs = [r["zcr"] for r in rows]
        sd_d = statistics.pstdev(durs)
        sd_r = statistics.pstdev(rmss)
        sd_z = statistics.pstdev(zcrs)
        within_dur.append(sd_d)
        within_rms.append(sd_r)
        within_zcr.append(sd_z)
        print(f"  {name:10s} dur {min(durs):.3f}~{max(durs):.3f}s (σ={sd_d:.4f})  "
              f"rms σ={sd_r:.5f}  zcr σ={sd_z:.5f}")

    # ---- 组间差异 ----
    print()
    print("=" * 92)
    print(" 组间差异（不同情绪之间）")
    print("=" * 92)
    base = groups.get("baseline", [])
    if base:
        bd = statistics.mean([r["dur"] for r in base])
        br = statistics.mean([r["rms"] for r in base])
        bz = statistics.mean([r["zcr"] for r in base])
        print(f"  基线均值: dur={bd:.3f}s rms={br:.5f} zcr={bz:.5f}\n")
        print(f"  {'case':10s} {'dur均值':>9s} {'Δdur':>9s} {'Δrms':>9s} {'Δzcr':>9s}")
        print("  " + "-" * 52)
        for name, rows in groups.items():
            if not rows or name == "baseline":
                continue
            md = statistics.mean([r["dur"] for r in rows])
            mr = statistics.mean([r["rms"] for r in rows])
            mz = statistics.mean([r["zcr"] for r in rows])
            print(f"  {name:10s} {md:8.3f}s {md-bd:+8.3f}s "
                  f"{mr-br:+9.5f} {mz-bz:+9.5f}")

    # ---- 判定 ----
    print()
    print("=" * 92)
    print(" 判定")
    print("=" * 92)
    if within_dur:
        w = statistics.mean(within_dur)
        print(f"\n  组内时长 σ 均值 = {w:.4f}s")
        # 组间：各情绪均值相对基线的偏离
        if base:
            bd = statistics.mean([r["dur"] for r in base])
            between = [
                abs(statistics.mean([r["dur"] for r in rows]) - bd)
                for name, rows in groups.items()
                if rows and name != "baseline"
            ]
            if between:
                b = statistics.mean(between)
                print(f"  组间时长 |Δ| 均值 = {b:.4f}s")
                ratio = b / w if w > 1e-6 else float("inf")
                print(f"  组间/组内 比值 = {ratio:.2f}")
                print()
                if ratio < 3:
                    print("  [结论] 组间差异与采样噪声同量级 →")
                    print("         emotion/instruction **没有可辨识的模型级影响**")
                else:
                    print("  [结论] 组间差异显著大于噪声 → 情绪可能起作用，需听测确认")
    (OUT / "groups.json").write_text(
        json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  结果已存: {OUT/'groups.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
