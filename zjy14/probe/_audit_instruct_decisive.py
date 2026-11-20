"""决定性实验：指令文本到底进没进模型？

## 上一轮的关键线索

  A. zero_shot 无 spk  + 参考文本   → md5 9d1eb78d4001  dur 4.92s
  B. instruct2 无 spk  + 悲伤指令   → md5 f418c890141d  dur 7.68s
  C. instruct2 有 spk  + 悲伤指令   → md5 cdd7a507f832  dur 12.44s
  D. zero_shot 有 spk              → md5 9d1eb78d4001  dur 4.92s

观察：A 与 D **逐字节相同** —— 说明 spk2info 里存的就是同一份 prompt。
而 C 与 D 差异巨大，但**这不能证明指令生效**，因为
`frontend_instruct2` 无论有没有 spk_id 都会执行：

    del model_input['llm_prompt_speech_token']
    del model_input['llm_prompt_speech_token_len']

删掉这两个字段会改变 LLM 的 in-context 结构（失去韵律锚点），
这本身就足以让输出变化 —— 与指令内容无关。

## 所以必须这样测

固定 spk_id，**只改指令文本**：

  若「悲伤指令」与「开心指令」输出逐字节相同
      → 指令被丢弃（spk2info 的 prompt_text 覆盖了 instruct_text）
  若不同
      → 指令真的进了模型

同时对「无 spk_id」路径做同样测试作为对照。

用法：
  python _audit_decisive.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "_audit_instruct_decisive"
TEXT = "今天我们一起去看海吧，海边的风应该会很舒服。"
VOICE_WAV = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav"
MODEL_DIR = os.path.expanduser("~/models/Fun-CosyVoice3-0.5B-2512")

PROMPT_TEXT = (
    "You are a helpful assistant.<|endofprompt|>"
    "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀"
)

#: 三条**语义完全相反**的指令。若输出相同，说明指令未进入模型。
INSTRUCTS = [
    ("sad",   "请用非常悲伤、低沉、缓慢、想哭的语气说这句话。<|endofprompt|>"),
    ("happy", "请用非常开心、高昂、快速、兴奋的语气说这句话。<|endofprompt|>"),
    ("angry", "请用愤怒、大声、急促、咆哮的语气说这句话。<|endofprompt|>"),
]


def analyze(pcm: bytes) -> dict:
    import array
    import math

    arr = array.array("h")
    arr.frombytes(pcm)
    n = len(arr)
    if n == 0:
        return {"dur": 0, "rms": 0, "zcr": 0, "f0": None}
    rms = math.sqrt(sum((v / 32768.0) ** 2 for v in arr) / n)
    zcr = sum(1 for i in range(1, n) if (arr[i] >= 0) != (arr[i - 1] >= 0)) / n

    f0s = []
    win = 1024
    for start in range(0, max(1, n - win), win * 2):
        seg = arr[start:start + win]
        if len(seg) < win:
            break
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
            f0s.append(24000.0 / best_lag)
    f0 = sorted(f0s)[len(f0s) // 2] if f0s else None
    return {"dur": round(n / 24000, 4), "rms": round(rms, 5),
            "zcr": round(zcr, 5), "f0": round(f0, 1) if f0 else None}


def run_case(model, torch, name, kind, use_spk, instruct=None):
    torch.manual_seed(1986)
    torch.cuda.manual_seed_all(1986)
    kw = {"zero_shot_spk_id": "elysia"} if use_spk else {}
    try:
        if kind == "instruct":
            gen = model.inference_instruct2(
                TEXT, instruct, VOICE_WAV, stream=False, speed=1.0, **kw)
        else:
            gen = model.inference_zero_shot(
                TEXT, "" if use_spk else PROMPT_TEXT,
                VOICE_WAV, stream=False, speed=1.0, **kw)
        chunks = list(gen)
    except Exception as exc:  # noqa: BLE001
        print(f"    [FAIL] {type(exc).__name__}: {str(exc)[:90]}")
        return None

    if not chunks:
        return None
    speech = chunks[0]["tts_speech"]
    pcm = (speech.cpu().numpy().flatten() * 32767).astype("<i2").tobytes()
    a = analyze(pcm)
    a["md5"] = hashlib.md5(pcm).hexdigest()[:12]
    (OUT / f"{name}.pcm").write_bytes(pcm)
    print(f"    dur={a['dur']:6.3f}s  f0={a['f0']}Hz  rms={a['rms']:.5f}  "
          f"zcr={a['zcr']:.5f}  md5={a['md5']}")
    return a


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("=" * 88)
    print(" 决定性实验：固定 spk_id，只改指令文本")
    print("=" * 88)

    sys.path.insert(0, os.path.expanduser("~/CosyVoice"))
    sys.path.insert(0, os.path.expanduser("~/CosyVoice/third_party/Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import CosyVoice3  # type: ignore
    import torch  # type: ignore

    print("\n  加载模型 ...")
    model = CosyVoice3(MODEL_DIR, load_vllm=False, fp16=False)
    model.add_zero_shot_spk(PROMPT_TEXT, VOICE_WAV, "elysia")
    print("  音色 elysia 已注册\n")

    results: dict[str, dict] = {}

    # ---- 组 1：instruct2 + spk_id（项目若改用 instruct2 会走这条）----
    print("  [组 1] inference_instruct2 + zero_shot_spk_id='elysia'")
    print("         （spk2info 提供 prompt_text，instruct_text 可能被覆盖）")
    for tag, instr in INSTRUCTS:
        print(f"    -- {tag}")
        r = run_case(model, torch, f"spk_{tag}", "instruct", True, instr)
        if r:
            results[f"spk_{tag}"] = r

    # ---- 组 2：instruct2 无 spk_id（指令直接当 prompt_text 用）----
    print("\n  [组 2] inference_instruct2 无 spk_id（指令直接进 prompt_text）")
    for tag, instr in INSTRUCTS:
        print(f"    -- {tag}")
        r = run_case(model, torch, f"nospk_{tag}", "instruct", False, instr)
        if r:
            results[f"nospk_{tag}"] = r

    # ---- 组 3：基线 ----
    print("\n  [组 3] 基线：zero_shot + spk_id（项目当前实际行为）")
    r = run_case(model, torch, "baseline", "zero", True)
    if r:
        results["baseline"] = r

    # ---- 判定 ----
    print()
    print("=" * 88)
    print(" 判定")
    print("=" * 88)

    def same(a: str, b: str) -> bool | None:
        ra, rb = results.get(a), results.get(b)
        if not ra or not rb:
            return None
        return ra["md5"] == rb["md5"]

    print("\n  [组 1] 有 spk_id 时，换指令输出是否变化？")
    s1 = same("spk_sad", "spk_happy")
    s2 = same("spk_sad", "spk_angry")
    if s1 is None or s2 is None:
        print("    数据不足")
    elif s1 and s2:
        print("    sad == happy == angry  → **指令被完全丢弃**")
        print("    根因：spk2info 里的 prompt_text 覆盖了 instruct_text")
    else:
        print(f"    sad vs happy 相同? {s1}   sad vs angry 相同? {s2}")
        print("    → 指令**确实进入了模型**")

    print("\n  [组 2] 无 spk_id 时，换指令输出是否变化？")
    n1 = same("nospk_sad", "nospk_happy")
    n2 = same("nospk_sad", "nospk_angry")
    if n1 is None or n2 is None:
        print("    数据不足")
    elif n1 and n2:
        print("    也全部相同 → 模型本身不支持该指令")
    else:
        print(f"    sad vs happy 相同? {n1}   sad vs angry 相同? {n2}")
        print("    → 指令**生效**（此路径可用）")

    print("\n  [组 3] 基线 vs 组1(sad)")
    b = same("baseline", "spk_sad")
    if b is not None:
        print(f"    相同? {b}")
        if not b:
            rb, rs = results["baseline"], results["spk_sad"]
            print(f"    差异来自 `del llm_prompt_speech_token`（失去韵律锚点），")
            print(f"    而非指令内容：Δdur={rs['dur']-rb['dur']:+.3f}s  "
                  f"Δf0={(rs['f0']-rb['f0']) if (rs['f0'] and rb['f0']) else 0:+.1f}Hz")

    (OUT / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  结果已存: {OUT/'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
