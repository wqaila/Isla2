"""D1 情绪验证：6 种情绪是否真的能被表达出来。

背景：
  原提示词的 D1 决策点一直未完成。Phase 0 那次验证用的是**参考文本被污染前**
  的配置，且只做了单次采样（后来发现采样不可靠）。

本次验证的设计要点：
  1. 用已修好的参考文本（85 字，语速 4.33 字/秒）
  2. 每组情绪 **3 次采样**，避免单次随机性误导
  3. 同时导出盲听样本，供人工判断（客观指标不能替代听感）
  4. 包含中性基线作对照

情绪清单（决策 D4）：
  官方 3 条：happy / sad / angry
  实验 2 条：comforting / playful（自造指令，标注未验证）
  基线：neutral（无指令）

评价维度：
  客观（仅用于筛选，不能作为结论）：
    - F0 中位数与起伏：情绪通常伴随音高变化
    - 能量包络变异：激动情绪能量起伏更大
    - 语速（字符/秒）：慢速/快速是明确的情绪线索
  主观（决定性的）：
    - 导出 A/B 样本，人工盲听
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
REF_WAV = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
REF_TXT = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_d1_emotion")
OUT.mkdir(parents=True, exist_ok=True)

PREFIX = "You are a helpful assistant.<|endofprompt|>"
SR = 24000
SAMPLES_PER_EMOTION = 3

# 合成文本：足够长以保证文本长度比达标（> 0.5）
# 参考文本 85 字符，这里用 ~50 字，比值约 0.59
TEXT = (
    "今天天气真不错，我们一起出去走走吧。"
    "昨天下了整整一天的雨，窗外的树叶被打得七零八落。"
)

# 情绪 → 指令文本
# 官方 3 条来自 cosyvoice/utils/common.py:28 的 instruct_list
# 实验 2 条为自造，效果待验证
EMOTIONS: dict[str, dict[str, str]] = {
    "neutral": {
        "instruction": "",
        "source": "基线（无指令）",
        "official": "yes",
    },
    "happy": {
        "instruction": PREFIX + "请非常开心地说一句话。",
        "source": "官方 instruct_list",
        "official": "yes",
    },
    "sad": {
        "instruction": PREFIX + "请非常伤心地说一句话。",
        "source": "官方 instruct_list",
        "official": "yes",
    },
    "angry": {
        "instruction": PREFIX + "请非常生气地说一句话。",
        "source": "官方 instruct_list",
        "official": "yes",
    },
    "comforting": {
        "instruction": PREFIX + "请用温柔安慰的语气说一句话。",
        "source": "自造（实验性）",
        "official": "no",
    },
    "playful": {
        "instruction": PREFIX + "请用俏皮活泼的语气说一句话。",
        "source": "自造（实验性）",
        "official": "no",
    },
}


# ----------------------------------------------------------------------
# 特征提取
# ----------------------------------------------------------------------


def _frames(x: np.ndarray, sr: int, win_s: float = 0.04, hop_s: float = 0.01):
    win, hop = int(sr * win_s), int(sr * hop_s)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return np.zeros((0, win)), hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return x[idx].astype(np.float64), hop


def f0_stats(x: np.ndarray, sr: int, fmin: float = 70.0, fmax: float = 450.0) -> dict:
    """基频统计：中位数、p10/p90、变异系数。"""
    fr, _ = _frames(x, sr)
    if fr.shape[0] == 0:
        return {"f0": 0.0, "f0_p10": 0.0, "f0_p90": 0.0, "f0cv": 0.0}
    e = np.sqrt((fr**2).mean(axis=1))
    fr = fr[e >= np.percentile(e, 70)]
    if fr.shape[0] == 0:
        return {"f0": 0.0, "f0_p10": 0.0, "f0_p90": 0.0, "f0cv": 0.0}

    lo, hi = int(sr / fmax), int(sr / fmin)
    out = []
    for f in fr:
        f = f - f.mean()
        ac = np.correlate(f, f, mode="full")[len(f) - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        seg = ac[lo:min(hi + 1, len(ac))]
        if seg.size < 3:
            continue
        peaks = [
            i for i in range(1, len(seg) - 1)
            if seg[i] > seg[i - 1] and seg[i] >= seg[i + 1] and seg[i] > 0.35
        ]
        if not peaks:
            continue
        best = max(peaks, key=lambda i: seg[i])
        half = best // 2
        if half >= lo and half < len(ac) and ac[half] > 0.5 * ac[lo + best]:
            best = half
        out.append(sr / (lo + best))

    if not out:
        return {"f0": 0.0, "f0_p10": 0.0, "f0_p90": 0.0, "f0cv": 0.0}
    arr = np.array(out)
    return {
        "f0": float(np.median(arr)),
        "f0_p10": float(np.percentile(arr, 10)),
        "f0_p90": float(np.percentile(arr, 90)),
        "f0cv": float(np.std(arr) / max(np.mean(arr), 1e-9)),
    }


def energy_stats(x: np.ndarray, sr: int) -> dict:
    """能量包络统计。"""
    fr, _ = _frames(x, sr)
    if fr.shape[0] == 0:
        return {"env_cv": 0.0, "rms_db": -99.0}
    e = np.sqrt((fr**2).mean(axis=1))
    voiced = e[e > e.max() * 0.05] if e.max() > 0 else e
    env_cv = float(voiced.std() / max(voiced.mean(), 1e-9)) if voiced.size else 0.0
    rms = float(np.sqrt((x.astype(np.float64) ** 2).mean()))
    return {
        "env_cv": env_cv,
        "rms_db": float(20 * np.log10(max(rms, 1e-9))),
    }


def syllable_stats(x: np.ndarray, sr: int) -> dict:
    """音节时长变异系数（机械感指标）。"""
    fr, hop = _frames(x, sr)
    if fr.shape[0] < 10:
        return {"syl_cv": 0.0, "syl_n": 0}
    e = np.sqrt((fr**2).mean(axis=1))
    if e.max() <= 0:
        return {"syl_cv": 0.0, "syl_n": 0}
    voiced = e > e.max() * 0.05
    idx = np.where(voiced)[0]
    if idx.size == 0:
        return {"syl_cv": 0.0, "syl_n": 0}
    seg = voiced[idx[0]: idx[-1] + 1]
    lengths = []
    i = 0
    while i < len(seg):
        if seg[i]:
            j = i
            while j < len(seg) and seg[j]:
                j += 1
            lengths.append((j - i) * hop / sr)
            i = j
        else:
            i += 1
    if len(lengths) < 3:
        return {"syl_cv": 0.0, "syl_n": len(lengths)}
    arr = np.array(lengths)
    return {
        "syl_cv": float(arr.std() / max(arr.mean(), 1e-9)),
        "syl_n": len(lengths),
    }


def describe(x: np.ndarray, sr: int = SR) -> dict:
    """综合特征。"""
    d = {"dur": round(len(x) / sr, 2)}
    d.update({k: round(v, 3) if isinstance(v, float) else v
              for k, v in f0_stats(x, sr).items()})
    d.update({k: round(v, 3) if isinstance(v, float) else v
              for k, v in energy_stats(x, sr).items()})
    d.update({k: round(v, 3) if isinstance(v, float) else v
              for k, v in syllable_stats(x, sr).items()})
    # 语速：字数 / 有声时长
    voiced_s = d["dur"] * 0.85  # 粗略估计，用于相对比较
    d["chars_per_s"] = round(len(TEXT) / max(d["dur"], 0.1), 2)
    return d


# ----------------------------------------------------------------------
# 合成
# ----------------------------------------------------------------------


def synth_hybrid(model, text: str, instruction: str, voice: str = "elysia"):
    """混合调用：缓存音色条件 + 替换 prompt_text 为指令。

    已验证：保留 llm_prompt_speech_token 会在卷积层报错，
    必须删除（对齐官方 instruct2 语义）。
    """
    cached = dict(model.frontend.spk2info[voice])
    ttk, ttl = model.frontend._extract_text_token(text)

    if instruction:
        itk, itl = model.frontend._extract_text_token(instruction)
        prompt_text, prompt_text_len = itk, itl
    else:
        # 中性基线：用参考文本（走正常 zero_shot 语义）
        prompt_text = cached["prompt_text"]
        prompt_text_len = cached["prompt_text_len"]

    mi = {
        "text": ttk, "text_len": ttl,
        "prompt_text": prompt_text, "prompt_text_len": prompt_text_len,
        "flow_prompt_speech_token": cached["flow_prompt_speech_token"],
        "flow_prompt_speech_token_len": cached["flow_prompt_speech_token_len"],
        "prompt_speech_feat": cached["prompt_speech_feat"],
        "prompt_speech_feat_len": cached["prompt_speech_feat_len"],
        "llm_embedding": cached["llm_embedding"],
        "flow_embedding": cached["flow_embedding"],
    }
    # 关键：删除 llm_prompt_speech_token，否则卷积层报错
    return list(model.model.tts(**mi, stream=False, speed=1.0))


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3

    sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")
    from custom_voice.inference.text_frontend import enable_wetext_frontend

    print("=" * 100)
    print(" D1 情绪验证：6 种情绪 × 3 次采样")
    print("=" * 100)

    ref_text = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" not in ref_text:
        ref_text = PREFIX + ref_text

    print(f"\n参考文本: {len(ref_text)} 字符")
    print(f"合成文本: {len(TEXT)} 字符  比值 {len(TEXT)/len(ref_text):.2f}")
    if len(TEXT) / len(ref_text) < 0.5:
        print("  [警告] 文本长度比低于 0.5，音色可能漂移")

    print("\n加载模型...")
    t0 = time.time()
    cosy = CosyVoice3(str(MODEL), fp16=False)
    print(f"  加载 {time.time()-t0:.1f}s")

    # 启用文本前端
    try:
        enable_wetext_frontend(cosy.frontend)
        print(f"  文本前端: {cosy.frontend.text_frontend!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  文本前端启用失败: {exc}")

    cosy.add_zero_shot_spk(ref_text, str(REF_WAV), "elysia")
    print("  音色已注册")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    # 预热
    reset_hop()
    list(cosy.inference_zero_shot("你好。", "", str(REF_WAV),
                                  zero_shot_spk_id="elysia", stream=False))
    print("  预热完成")

    # ---- 逐情绪合成 ----
    results: dict[str, list[dict]] = {}
    print()
    print("=" * 100)
    print(" 合成")
    print("=" * 100)

    for emotion, spec in EMOTIONS.items():
        print(f"\n--- {emotion} ({spec['source']}) ---")
        if spec["instruction"]:
            print(f"    指令: {spec['instruction'][:60]}...")
        else:
            print("    指令: （无，走基线路径）")

        samples = []
        for i in range(SAMPLES_PER_EMOTION):
            reset_hop()
            t0 = time.time()
            try:
                chunks = synth_hybrid(cosy, TEXT, spec["instruction"])
            except Exception as exc:  # noqa: BLE001
                print(f"    第{i+1}次失败: {type(exc).__name__}: {exc}")
                continue
            if not chunks:
                print(f"    第{i+1}次无输出")
                continue

            parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                     for c in chunks]
            y = np.concatenate(parts) if len(parts) > 1 else parts[0]
            path = OUT / f"{emotion}_{i+1}.wav"
            sf.write(str(path), y, SR, subtype="PCM_16")

            d = describe(y, SR)
            d["elapsed_s"] = round(time.time() - t0, 2)
            d["file"] = path.name
            samples.append(d)
            print(
                f"    第{i+1}次  {d['dur']:5.2f}s  "
                f"F0 {d['f0']:6.1f}Hz[{d['f0_p10']:.0f}-{d['f0_p90']:.0f}]  "
                f"起伏 {d['f0cv']:.3f}  能量CV {d['env_cv']:.3f}  "
                f"音节CV {d['syl_cv']:.3f}  语速 {d['chars_per_s']:.2f}字/秒"
            )

        if samples:
            results[emotion] = samples

    # ---- 汇总：取中位数 ----
    print()
    print("=" * 100)
    print(" 汇总（每种情绪取 3 次的中位数）")
    print("=" * 100)
    print(f"  {'情绪':<12}{'时长':>7}{'F0':>9}{'F0起伏':>9}"
          f"{'能量CV':>9}{'音节CV':>9}{'语速':>9}  来源")
    print("  " + "-" * 88)

    summary: dict[str, dict] = {}
    for emotion, samples in results.items():
        med = {
            k: float(np.median([s[k] for s in samples]))
            for k in ("dur", "f0", "f0_p10", "f0_p90", "f0cv",
                      "env_cv", "syl_cv", "chars_per_s")
        }
        summary[emotion] = med
        print(
            f"  {emotion:<12}{med['dur']:>6.2f}s{med['f0']:>9.1f}"
            f"{med['f0cv']:>9.3f}{med['env_cv']:>9.3f}"
            f"{med['syl_cv']:>9.3f}{med['chars_per_s']:>9.2f}  "
            f"{EMOTIONS[emotion]['source']}"
        )

    # ---- 与中性基线的差异 ----
    print()
    print("=" * 100)
    print(" 相对中性基线的偏离（客观筛选，不能作为结论）")
    print("=" * 100)

    if "neutral" in summary:
        base = summary["neutral"]
        print(f"  {'情绪':<12}{'F0差':>9}{'F0起伏差':>11}"
              f"{'能量CV差':>11}{'音节CV差':>11}{'语速差':>9}  区分度")
        print("  " + "-" * 80)
        for emotion, med in summary.items():
            if emotion == "neutral":
                continue
            df0 = med["f0"] - base["f0"]
            dcv = med["f0cv"] - base["f0cv"]
            denv = med["env_cv"] - base["env_cv"]
            dsyl = med["syl_cv"] - base["syl_cv"]
            dspeed = med["chars_per_s"] - base["chars_per_s"]

            # 区分度：任一项超过阈值就算有区分
            signals = []
            if abs(df0) > 15:
                signals.append("F0")
            if abs(dcv) > 0.03:
                signals.append("起伏")
            if abs(denv) > 0.05:
                signals.append("能量")
            if abs(dsyl) > 0.08:
                signals.append("节奏")
            if abs(dspeed) > 0.3:
                signals.append("语速")

            print(
                f"  {emotion:<12}{df0:>+9.1f}{dcv:>+11.3f}"
                f"{denv:>+11.3f}{dsyl:>+11.3f}{dspeed:>+9.2f}  "
                f"{'、'.join(signals) if signals else '无显著差异'}"
            )

    # ---- 保存结果 ----
    meta = {
        "text": TEXT,
        "ref_text_len": len(ref_text),
        "text_ratio": round(len(TEXT) / len(ref_text), 3),
        "samples_per_emotion": SAMPLES_PER_EMOTION,
        "emotions": {
            k: {"instruction": v["instruction"], "source": v["source"],
                "official": v["official"]}
            for k, v in EMOTIONS.items()
        },
        "raw": results,
        "median": summary,
    }
    (OUT / "result.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print("=" * 100)
    print(" 输出")
    print("=" * 100)
    print(f"  目录: {OUT}")
    print(f"  样本: {sum(len(v) for v in results.values())} 个 WAV")
    print(f"  数据: {OUT / 'result.json'}")
    print()
    print("  >>> 客观指标只能筛掉明显无效的，情绪是否真的表达必须人工盲听。")
    print("  >>> 建议对比听：neutral_1 vs happy_1 vs sad_1 vs angry_1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
