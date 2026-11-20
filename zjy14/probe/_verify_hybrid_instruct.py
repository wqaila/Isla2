"""验证「混合方案」：保留 spk2info 缓存 + 注入指令，能否既快又让情绪生效。

## 背景

审计已确认两件事：

1. `frontend_instruct2` 在传 `zero_shot_spk_id` 时**静默丢弃 instruct_text**
   （因为 `frontend_zero_shot` 走 `{**spk2info[id]}` 分支，不读 prompt_text）
2. 不传 spk_id 时指令生效，但每次要重新编码参考音频（实测慢 16.5%）

## 本脚本验证的混合方案

`add_zero_shot_spk` 存进 spk2info 的是**完整** model_input（含
`llm_prompt_speech_token`）。而 `frontend_instruct2` 的关键动作是删掉它。

所以可以手工拼：

    model_input = dict(spk2info[voice])          # 复用缓存，省掉编码
    del model_input['llm_prompt_speech_token']   # 让指令进入 LLM
    del model_input['llm_prompt_speech_token_len']
    model_input['prompt_text'] = instruct_tokens # 注入指令
    model_input['prompt_text_len'] = ...
    model_input['text'] = tts_text_tokens
    model_input['text_len'] = ...

这样理论上：**保留缓存的音色嵌入与 flow 特征（快），同时让指令进 LLM（生效）**。

## 判据

1. 换指令输出是否不同（验证生效）
2. 耗时是否接近 zero_shot+spk（验证快）
3. 音色是否漂移（对比 rms/f0 是否落在合理范围）

用法：
  python _verify_hybrid_instruct.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "_verify_hybrid_instruct"
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


def analyze(pcm: bytes) -> dict:
    import array
    import math

    arr = array.array("h")
    arr.frombytes(pcm)
    n = len(arr)
    if n == 0:
        return {"dur": 0, "rms": 0, "f0": None}
    rms = math.sqrt(sum((v / 32768.0) ** 2 for v in arr) / n)
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
    return {
        "dur": round(n / 24000, 3),
        "rms": round(rms, 5),
        "f0": round(sorted(f0s)[len(f0s) // 2], 1) if f0s else None,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("=" * 88)
    print(" 混合方案验证：缓存音色 + 注入指令")
    print("=" * 88)

    sys.path.insert(0, os.path.expanduser("~/CosyVoice"))
    sys.path.insert(0, os.path.expanduser("~/CosyVoice/third_party/Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import CosyVoice3  # type: ignore
    import torch  # type: ignore

    print("\n  加载模型 ...")
    model = CosyVoice3(MODEL_DIR, load_vllm=False, fp16=False)
    model.add_zero_shot_spk(PROMPT_TEXT, VOICE_WAV, "elysia")
    fe = model.frontend

    cached = fe.spk2info["elysia"]
    print(f"  spk2info['elysia'] 字段: {sorted(cached.keys())}")
    print(f"  含 llm_prompt_speech_token: "
          f"{'是' if 'llm_prompt_speech_token' in cached else '否'}")

    def hybrid_tts(instruct: str):
        """手工拼 model_input：缓存音色 + 注入指令。"""
        tts_tok, tts_len = fe._extract_text_token(TEXT)
        ins_tok, ins_len = fe._extract_text_token(instruct)
        mi = dict(cached)                       # 浅拷贝，保留缓存
        mi.pop("llm_prompt_speech_token", None)  # 删掉，让指令进 LLM
        mi.pop("llm_prompt_speech_token_len", None)
        mi["prompt_text"] = ins_tok
        mi["prompt_text_len"] = ins_len
        mi["text"] = tts_tok
        mi["text_len"] = tts_len
        return model.model.tts(**mi, stream=False, speed=1.0)

    # ---- 基线：zero_shot + spk（项目当前路径）----
    print("\n  [基线] zero_shot + spk_id")
    torch.manual_seed(1986)
    torch.cuda.manual_seed_all(1986)
    t0 = time.perf_counter()
    try:
        base_chunks = list(model.inference_zero_shot(
            TEXT, "", VOICE_WAV, zero_shot_spk_id="elysia",
            stream=False, speed=1.0))
        base_ms = (time.perf_counter() - t0) * 1000
        bpcm = (base_chunks[0]["tts_speech"].cpu().numpy().flatten()
                * 32767).astype("<i2").tobytes()
        ba = analyze(bpcm)
        ba["md5"] = hashlib.md5(bpcm).hexdigest()[:12]
        ba["ms"] = round(base_ms)
        (OUT / "baseline.pcm").write_bytes(bpcm)
        print(f"    dur={ba['dur']}s f0={ba['f0']}Hz rms={ba['rms']} "
              f"md5={ba['md5']} 耗时={ba['ms']}ms")
    except Exception as exc:  # noqa: BLE001
        print(f"    [FAIL] {type(exc).__name__}: {str(exc)[:90]}")
        ba = None

    # ---- 混合方案 ----
    results = {}
    for tag, instr in INSTRUCTS:
        print(f"\n  [混合] {tag}")
        torch.manual_seed(1986)
        torch.cuda.manual_seed_all(1986)
        t0 = time.perf_counter()
        try:
            chunks = list(hybrid_tts(instr))
            ms = (time.perf_counter() - t0) * 1000
            if not chunks:
                print("    [FAIL] 无输出")
                continue
            pcm = (chunks[0]["tts_speech"].cpu().numpy().flatten()
                   * 32767).astype("<i2").tobytes()
            a = analyze(pcm)
            a["md5"] = hashlib.md5(pcm).hexdigest()[:12]
            a["ms"] = round(ms)
            results[tag] = a
            (OUT / f"hybrid_{tag}.pcm").write_bytes(pcm)
            print(f"    dur={a['dur']}s f0={a['f0']}Hz rms={a['rms']} "
                  f"md5={a['md5']} 耗时={a['ms']}ms")
        except Exception as exc:  # noqa: BLE001
            print(f"    [FAIL] {type(exc).__name__}: {str(exc)[:120]}")

    # ---- 对照：instruct2 无 spk（慢路径，已知生效）----
    print("\n  [对照] instruct2 无 spk_id（已知生效但慢）")
    ctrl = {}
    for tag, instr in INSTRUCTS:
        torch.manual_seed(1986)
        torch.cuda.manual_seed_all(1986)
        t0 = time.perf_counter()
        try:
            chunks = list(model.inference_instruct2(
                TEXT, instr, VOICE_WAV, stream=False, speed=1.0))
            ms = (time.perf_counter() - t0) * 1000
            pcm = (chunks[0]["tts_speech"].cpu().numpy().flatten()
                   * 32767).astype("<i2").tobytes()
            a = analyze(pcm)
            a["md5"] = hashlib.md5(pcm).hexdigest()[:12]
            a["ms"] = round(ms)
            ctrl[tag] = a
            print(f"    {tag}: dur={a['dur']}s f0={a['f0']}Hz "
                  f"md5={a['md5']} 耗时={a['ms']}ms")
        except Exception as exc:  # noqa: BLE001
            print(f"    {tag}: [FAIL] {str(exc)[:80]}")

    # ---- 判定 ----
    print()
    print("=" * 88)
    print(" 判定")
    print("=" * 88)

    if len(results) == 2:
        s, h = results["sad"], results["happy"]
        print(f"\n  1) 指令是否生效（混合方案）")
        if s["md5"] == h["md5"]:
            print(f"     sad == happy (md5 {s['md5']}) → **未生效**")
        else:
            print(f"     sad != happy → **生效**")
            print(f"     Δdur={h['dur']-s['dur']:+.3f}s  "
                  f"Δf0={(h['f0']-s['f0']) if (h['f0'] and s['f0']) else 0:+.1f}Hz  "
                  f"Δrms={h['rms']-s['rms']:+.5f}")

        if ba:
            print(f"\n  2) 速度对比（相对基线 {ba['ms']}ms）")
            for tag in ("sad", "happy"):
                if tag in results:
                    d = results[tag]["ms"] - ba["ms"]
                    pct = d / ba["ms"] * 100
                    print(f"     混合 {tag:6s}: {results[tag]['ms']:5d}ms "
                          f"({pct:+6.1f}%)")
            for tag in ("sad", "happy"):
                if tag in ctrl:
                    d = ctrl[tag]["ms"] - ba["ms"]
                    pct = d / ba["ms"] * 100
                    print(f"     instruct2 {tag:6s}: {ctrl[tag]['ms']:5d}ms "
                          f"({pct:+6.1f}%)  ← 已知的慢路径")

            print(f"\n  3) 音色一致性（基线 rms={ba['rms']} f0={ba['f0']}）")
            for tag in ("sad", "happy"):
                if tag in results:
                    r = results[tag]
                    print(f"     混合 {tag:6s}: rms={r['rms']} "
                          f"({(r['rms']-ba['rms'])/ba['rms']*100:+.0f}%)  "
                          f"f0={r['f0']}")

    (OUT / "results.json").write_text(
        json.dumps({"baseline": ba, "hybrid": results, "control": ctrl},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  结果已存: {OUT/'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
