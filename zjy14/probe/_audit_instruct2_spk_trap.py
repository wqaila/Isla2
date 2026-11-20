"""情绪控制审计：定位「指令被静默丢弃」的精确机制。

## 已确认的事实

1. `inference_instruct2` 要求文本里含 `<|endofprompt|>`，否则：
     AssertionError: <|endofprompt|> not detected in CosyVoice3 text or prompt_text

2. 项目能跑通 `inference_zero_shot(seg.text, "", audio_path, zero_shot_spk_id=voice)`，
   是因为**注册音色时**把带标记的参考文本存进了 spk2info。

## 本脚本要验证的假设

`frontend_instruct2` 的实现是：

    def frontend_instruct2(self, tts_text, instruct_text, prompt_wav, resample_rate, zero_shot_spk_id):
        model_input = self.frontend_zero_shot(tts_text, instruct_text, prompt_wav, resample_rate, zero_shot_spk_id)
        del model_input['llm_prompt_speech_token']
        del model_input['llm_prompt_speech_token_len']
        return model_input

而 `frontend_zero_shot` 里：

    if zero_shot_spk_id == '':
        ... 用 prompt_text(=instruct_text) 编码 ...
    else:
        model_input = {**self.spk2info[zero_shot_spk_id]}   # ← instruct_text 被完全忽略

**假设：传入 zero_shot_spk_id 时，instruct_text 被静默丢弃。**

这就是 docs/13 记录的「官方 inference_instruct2 在带缓存音色时会静默丢弃指令」。

## 实验设计（4 组对照）

  A. zero_shot  + 无 spk_id + 带标记参考文本   → 基线
  B. instruct2  + 无 spk_id + 强指令(带标记)    → 指令真正进入模型
  C. instruct2  + 有 spk_id + 强指令(带标记)    → 指令是否被丢弃
  D. zero_shot  + 有 spk_id                    → 项目当前的实际行为

如果 C 与 D 输出完全一致（且与 B 明显不同），假设成立。

用法：
  python _audit_instruct2_mechanism.py
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "_audit_instruct2_spk_trap"
TEXT = "今天我们一起去看海吧，海边的风应该会很舒服。"
VOICE_WAV = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav"
MODEL_DIR = os.path.expanduser("~/models/Fun-CosyVoice3-0.5B-2512")

#: 注册音色时用的参考文本。
#: 项目在 cosyvoice_engine.py:64 定义了 PROMPT_PREFIX，注册时补在真实参考文本前。
#: 真实参考文本见 zjy13/server/characters/voices/elysia/ref_elysia.txt
PROMPT_TEXT = (
    "You are a helpful assistant.<|endofprompt|>"
    "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀"
)

#: 强指令：明显的悲伤+低沉+缓慢
INSTRUCT = "请用非常悲伤、低沉、缓慢的语气说这句话。<|endofprompt|>"


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

    # 多点采样估 F0，取中位数（比单点稳健）
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

    return {
        "dur": round(n / 24000, 4),
        "rms": round(rms, 5),
        "zcr": round(zcr, 5),
        "f0": round(f0, 1) if f0 else None,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("=" * 92)
    print(" 指令被静默丢弃？—— 四组对照")
    print("=" * 92)

    sys.path.insert(0, os.path.expanduser("~/CosyVoice"))
    sys.path.insert(0, os.path.expanduser("~/CosyVoice/third_party/Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import CosyVoice3  # type: ignore
    import torch  # type: ignore

    print(f"\n  加载模型 ...")
    model = CosyVoice3(MODEL_DIR, load_vllm=False, fp16=False)
    print(f"  模型类: {type(model).__name__}")

    # 注册音色（与项目一致：用带标记的参考文本）
    print(f"\n  注册音色 elysia（参考文本含 endofprompt）...")
    try:
        model.add_zero_shot_spk(PROMPT_TEXT, VOICE_WAV, "elysia")
        print(f"  已注册。spk2info keys = {list(model.frontend.spk2info.keys())}")
        info = model.frontend.spk2info.get("elysia", {})
        print(f"  spk2info['elysia'] 字段: {sorted(info.keys())}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [警告] 注册失败: {type(exc).__name__}: {exc}")

    cases = [
        ("A_zero_shot_nospk",  "zero",     False),
        ("B_instruct2_nospk",  "instruct", False),
        ("C_instruct2_spk",    "instruct", True),
        ("D_zero_shot_spk",    "zero",     True),
    ]

    rows = []
    for name, kind, use_spk in cases:
        print(f"\n  --- {name} (kind={kind}, spk_id={'elysia' if use_spk else '无'}) ---")
        torch.manual_seed(1986)
        torch.cuda.manual_seed_all(1986)
        kw = {"zero_shot_spk_id": "elysia"} if use_spk else {}
        try:
            if kind == "instruct":
                gen = model.inference_instruct2(
                    TEXT, INSTRUCT, VOICE_WAV, stream=False, speed=1.0, **kw
                )
            else:
                gen = model.inference_zero_shot(
                    TEXT, PROMPT_TEXT if not use_spk else "",
                    VOICE_WAV, stream=False, speed=1.0, **kw
                )
            chunks = list(gen)
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {str(exc)[:100]}"
            print(f"      [FAIL] {msg}")
            rows.append({"case": name, "kind": kind, "spk": use_spk, "error": msg})
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
        rows.append({"case": name, "kind": kind, "spk": use_spk, "md5": md5, **a})
        print(f"      dur={a['dur']}s  f0={a['f0']}Hz  rms={a['rms']}  "
              f"zcr={a['zcr']}  md5={md5}")

    # ---- 汇总 ----
    print()
    print("=" * 92)
    print(" 汇总")
    print("=" * 92)
    ok = {r["case"]: r for r in rows if "error" not in r}
    if ok:
        print(f"\n  {'case':22s} {'dur':>8s} {'f0':>8s} {'rms':>9s} {'zcr':>8s} {'md5':>14s}")
        print("  " + "-" * 76)
        for r in rows:
            if "error" in r:
                print(f"  {r['case']:22s}  [FAIL] {r['error'][:40]}")
            else:
                print(f"  {r['case']:22s} {r['dur']:7.3f}s {str(r['f0']):>8s} "
                      f"{r['rms']:9.5f} {r['zcr']:8.5f} {r['md5']:>14s}")

        print(f"\n  关键对比：")
        b = ok.get("B_instruct2_nospk")
        c = ok.get("C_instruct2_spk")
        d = ok.get("D_zero_shot_spk")
        if c and d:
            same = c["md5"] == d["md5"]
            print(f"    C(instruct2+spk) vs D(zero_shot+spk) md5 相同? "
                  f"{'是 ← 指令被丢弃' if same else '否'}")
            if not same:
                print(f"      Δdur={c['dur']-d['dur']:+.3f}s  "
                      f"Δf0={(c['f0']-d['f0']) if (c['f0'] and d['f0']) else 0:+.1f}Hz")
        if b and d:
            print(f"    B(instruct2 无spk) vs D(zero_shot+spk) "
                  f"Δdur={b['dur']-d['dur']:+.3f}s  "
                  f"Δf0={(b['f0']-d['f0']) if (b['f0'] and d['f0']) else 0:+.1f}Hz")

    (OUT / "results.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  结果已存: {OUT/'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
