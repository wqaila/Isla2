"""验证分流策略：无情绪走 zero_shot（快），有情绪走 instruct2（生效）。

## 为什么需要这个

如果所有请求都改走 instruct2，那正常的无情绪对话也会失去
`add_zero_shot_spk` 的缓存优化（项目注释记录实测省 16.5%）。

所以方案应该是**分流**：

    无情绪  -> inference_zero_shot + spk_id   （保持现状，最快）
    有情绪  -> 混合注入（缓存音色 + 指令）      （生效）

本脚本用**同一文本、同一期望音频长度**对比两条路径的纯开销差异，
排除"音频变长导致耗时增加"的干扰。

方法：用一条**不改变时长**的轻指令（如"请稍微快一点点"）对比，
以及无指令基线。看纯指令开销有多大。

用法：
  python _verify_routing_cost.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "_verify_routing_cost"
TEXT = "今天我们一起去看海吧，海边的风应该会很舒服。"
VOICE_WAV = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav"
MODEL_DIR = os.path.expanduser("~/models/Fun-CosyVoice3-0.5B-2512")

PROMPT_TEXT = (
    "You are a helpful assistant.<|endofprompt|>"
    "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀"
)

#: 轻指令：不改变语速/情绪，只测纯指令开销
LIGHT_INSTRUCT = "You are a helpful assistant.<|endofprompt|>"


def analyze(pcm: bytes) -> dict:
    import array
    import math

    arr = array.array("h")
    arr.frombytes(pcm)
    n = len(arr)
    rms = math.sqrt(sum((v / 32768.0) ** 2 for v in arr) / n) if n else 0.0
    return {"dur": round(n / 24000, 3), "rms": round(rms, 5)}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("=" * 88)
    print(" 分流策略成本验证：纯指令开销（排除音频变长干扰）")
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

    def hybrid(instruct: str):
        tts_tok, tts_len = fe._extract_text_token(TEXT)
        ins_tok, ins_len = fe._extract_text_token(instruct)
        mi = dict(cached)
        mi.pop("llm_prompt_speech_token", None)
        mi.pop("llm_prompt_speech_token_len", None)
        mi["prompt_text"] = ins_tok
        mi["prompt_text_len"] = ins_len
        mi["text"] = tts_tok
        mi["text_len"] = tts_len
        return model.model.tts(**mi, stream=False, speed=1.0)

    ROUNDS = 3
    results = {"zero_shot": [], "hybrid_light": [], "instruct2_nospk": []}

    for r in range(ROUNDS):
        print(f"\n  --- 第 {r+1} 轮 ---")

        # A. zero_shot + spk（当前生产路径）
        torch.manual_seed(1986)
        torch.cuda.manual_seed_all(1986)
        t0 = time.perf_counter()
        chunks = list(model.inference_zero_shot(
            TEXT, "", VOICE_WAV, zero_shot_spk_id="elysia",
            stream=False, speed=1.0))
        ms = (time.perf_counter() - t0) * 1000
        pcm = (chunks[0]["tts_speech"].cpu().numpy().flatten()
               * 32767).astype("<i2").tobytes()
        a = analyze(pcm)
        a["ms"] = round(ms)
        a["md5"] = hashlib.md5(pcm).hexdigest()[:12]
        results["zero_shot"].append(a)
        print(f"    A zero_shot+spk   : {a['ms']:5d}ms  dur={a['dur']}s  "
              f"rms={a['rms']}  md5={a['md5']}")

        # B. 混合 + 轻指令
        torch.manual_seed(1986)
        torch.cuda.manual_seed_all(1986)
        t0 = time.perf_counter()
        chunks = list(hybrid(LIGHT_INSTRUCT))
        ms = (time.perf_counter() - t0) * 1000
        pcm = (chunks[0]["tts_speech"].cpu().numpy().flatten()
               * 32767).astype("<i2").tobytes()
        b = analyze(pcm)
        b["ms"] = round(ms)
        b["md5"] = hashlib.md5(pcm).hexdigest()[:12]
        results["hybrid_light"].append(b)
        print(f"    B 混合+轻指令     : {b['ms']:5d}ms  dur={b['dur']}s  "
              f"rms={b['rms']}  md5={b['md5']}")

        # C. instruct2 无 spk（慢路径）
        torch.manual_seed(1986)
        torch.cuda.manual_seed_all(1986)
        t0 = time.perf_counter()
        chunks = list(model.inference_instruct2(
            TEXT, LIGHT_INSTRUCT, VOICE_WAV, stream=False, speed=1.0))
        ms = (time.perf_counter() - t0) * 1000
        pcm = (chunks[0]["tts_speech"].cpu().numpy().flatten()
               * 32767).astype("<i2").tobytes()
        c = analyze(pcm)
        c["ms"] = round(ms)
        c["md5"] = hashlib.md5(pcm).hexdigest()[:12]
        results["instruct2_nospk"].append(c)
        print(f"    C instruct2 无spk : {c['ms']:5d}ms  dur={c['dur']}s  "
              f"rms={c['rms']}  md5={c['md5']}")

    # ---- 汇总 ----
    print()
    print("=" * 88)
    print(f" 汇总（{ROUNDS} 轮均值）")
    print("=" * 88)

    def avg(key: str, field: str):
        vals = [x[field] for x in results[key] if field in x]
        return sum(vals) / len(vals) if vals else 0

    base_ms = avg("zero_shot", "ms")
    base_dur = avg("zero_shot", "dur")
    print(f"\n  {'路径':22s} {'耗时':>9s} {'音频':>8s} {'RTF':>7s} {'相对基线':>10s}")
    print("  " + "-" * 62)
    for key, label in [
        ("zero_shot", "A zero_shot+spk"),
        ("hybrid_light", "B 混合+轻指令"),
        ("instruct2_nospk", "C instruct2 无spk"),
    ]:
        ms = avg(key, "ms")
        dur = avg(key, "dur")
        rtf = (ms / 1000) / dur if dur else 0
        pct = (ms - base_ms) / base_ms * 100 if base_ms else 0
        print(f"  {label:22s} {ms:8.0f}ms {dur:7.3f}s {rtf:7.3f} {pct:+9.1f}%")

    print()
    print("  判读：")
    print("    - 若 B 与 A 接近（<20%），说明分流策略代价小，可以只在有情绪时走 B")
    print("    - 若 B 明显更慢，说明指令路径本身有固定开销，需权衡")

    (OUT / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  结果已存: {OUT/'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
