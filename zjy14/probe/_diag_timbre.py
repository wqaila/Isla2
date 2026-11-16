"""音色诊断：定位"合成结果不像参考音色"的根因。

要回答三个问题：
  Q1 合成音频的基频/频谱特征与参考音频差多远？（客观量化）
  Q2 纯 zero_shot 基线（不带任何指令）是否也不像？——区分"引擎问题"与"我的混合调用问题"
  Q3 混合调用模式（F2）相比基线，音色是否退化？

方法：
  - 自相关法估基频（F0）中位数与分布
  - 频谱质心（spectral centroid）反映音色明亮度
  - 谐噪比（HNR）粗略反映"是否像人声"
  - 与参考音频逐项对比

输出到 stdout，同时导出 A/B 音频供人工确认。
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

MODEL = "/home/user/models/Fun-CosyVoice3-0.5B-2512"
REF_WAV = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav"
REF_TXT = "/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt"
PREFIX = "You are a helpful assistant.<|endofprompt|>"
OUT = Path("/mnt/d/xiangmudaima/zjy14/_timbre_diag")
OUT.mkdir(parents=True, exist_ok=True)

SR = 24000


# ---------------------------------------------------------------- 特征提取


def frame_signal(x: np.ndarray, sr: int, win_s: float = 0.04, hop_s: float = 0.02):
    """分帧，返回 (帧数, 帧长) 矩阵。"""
    win = int(sr * win_s)
    hop = int(sr * hop_s)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return np.zeros((0, win), dtype=np.float64)
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return x[idx].astype(np.float64)


def estimate_f0(x: np.ndarray, sr: int, fmin: float = 60.0, fmax: float = 500.0) -> np.ndarray:
    """自相关法估基频。返回有声帧的 F0 数组（Hz）。"""
    frames = frame_signal(x, sr)
    if frames.shape[0] == 0:
        return np.zeros(0)

    # 只保留能量足够的帧
    energy = np.sqrt((frames**2).mean(axis=1))
    thr = np.percentile(energy, 60)
    frames = frames[energy >= thr]
    if frames.shape[0] == 0:
        return np.zeros(0)

    lag_min = int(sr / fmax)
    lag_max = int(sr / fmin)
    f0s = []
    for fr in frames:
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, mode="full")[len(fr) - 1 :]
        if ac[0] <= 0:
            continue
        seg = ac[lag_min : min(lag_max + 1, len(ac))]
        if seg.size == 0:
            continue
        # 找第一个显著峰（避免倍频误判）
        peak = int(np.argmax(seg)) + lag_min
        if ac[peak] < 0.3 * ac[0]:
            continue
        f0s.append(sr / peak)
    return np.array(f0s)


def spectral_centroid(x: np.ndarray, sr: int) -> float:
    """频谱质心（Hz）。反映音色明亮度。"""
    frames = frame_signal(x, sr)
    if frames.shape[0] == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(frames * np.hanning(frames.shape[1]), axis=1))
    freqs = np.fft.rfftfreq(frames.shape[1], 1.0 / sr)
    mag = spec.sum(axis=0)
    total = mag.sum()
    if total <= 0:
        return 0.0
    return float((mag * freqs).sum() / total)


def hnr(x: np.ndarray, sr: int) -> float:
    """谐噪比（dB）。越高越"像有声带的语音"。"""
    frames = frame_signal(x, sr)
    if frames.shape[0] == 0:
        return 0.0
    vals = []
    for fr in frames[:200]:
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, mode="full")[len(fr) - 1 :]
        if ac[0] <= 0:
            continue
        lag_min, lag_max = int(sr / 500), int(sr / 60)
        seg = ac[lag_min : min(lag_max, len(ac))]
        if seg.size == 0:
            continue
        r = np.max(seg) / ac[0]
        if 0 < r < 1:
            vals.append(10 * np.log10(r / (1 - r)))
    return float(np.median(vals)) if vals else 0.0


def describe(name: str, x: np.ndarray, sr: int = SR) -> dict:
    f0 = estimate_f0(x, sr)
    d = {
        "name": name,
        "dur": round(len(x) / sr, 2),
        "rms_db": round(20 * np.log10(max(np.sqrt((x**2).mean()), 1e-9)), 1),
        "peak": round(float(np.max(np.abs(x))) if len(x) else 0.0, 3),
        "f0_median": round(float(np.median(f0)), 1) if f0.size else 0.0,
        "f0_p10": round(float(np.percentile(f0, 10)), 1) if f0.size else 0.0,
        "f0_p90": round(float(np.percentile(f0, 90)), 1) if f0.size else 0.0,
        "f0_std": round(float(np.std(f0)), 1) if f0.size else 0.0,
        "f0_frames": int(f0.size),
        "centroid": round(spectral_centroid(x, sr), 1),
        "hnr_db": round(hnr(x, sr), 1),
    }
    return d


def print_row(d: dict) -> None:
    print(
        f"{d['name']:<26}{d['dur']:>6.2f}s{d['rms_db']:>8.1f}dB"
        f"{d['f0_median']:>8.1f}Hz[{d['f0_p10']:.0f}-{d['f0_p90']:.0f}]"
        f"std{d['f0_std']:>5.1f}{d['centroid']:>8.0f}Hz{d['hnr_db']:>7.1f}dB"
    )


# ---------------------------------------------------------------- 主流程


def main() -> int:
    print("=" * 96)
    print(" 音色诊断")
    print("=" * 96)

    # ---- 参考音频 ----
    ref, ref_sr = sf.read(REF_WAV, dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    print(f"\n参考音频: {REF_WAV}")
    print(f"  采样率 {ref_sr}  时长 {len(ref)/ref_sr:.2f}s")
    ref_desc = describe("00_参考音频(原始)", ref, ref_sr)
    print_row(ref_desc)

    # ---- 已有样本 ----
    print("\n" + "-" * 96)
    print(" 已有样本（Phase 0 导出）")
    print("-" * 96)
    probe_dir = Path("/mnt/d/xiangmudaima/zjy14/_audio_probe")
    existing = []
    if probe_dir.is_dir():
        for p in sorted(probe_dir.glob("*.wav")):
            x, sr = sf.read(str(p), dtype="float32")
            if x.ndim > 1:
                x = x.mean(axis=1)
            d = describe(p.stem, x, sr)
            existing.append(d)
            print_row(d)

    # ---- 重新生成干净对照 ----
    print("\n" + "-" * 96)
    print(" 重新生成对照样本")
    print("-" * 96)

    from cosyvoice.cli.cosyvoice import CosyVoice3

    ref_text = open(REF_TXT, encoding="utf-8").read().strip()
    if "<|endofprompt|>" not in ref_text:
        ref_text = PREFIX + ref_text

    print("加载模型...")
    cosy = CosyVoice3(MODEL, fp16=False)
    cosy.add_zero_shot_spk(ref_text, REF_WAV, "elysia")

    TEXT = "今天天气真不错，我们一起出去走走吧。"
    fresh = []

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    def save_and_measure(name: str, chunks) -> None:
        if not chunks:
            print(f"  [{name}] 无输出")
            return
        x = chunks[0]["tts_speech"].detach().cpu().float().numpy().squeeze()
        sf.write(str(OUT / f"{name}.wav"), x, cosy.sample_rate, subtype="PCM_16")
        d = describe(name, x, cosy.sample_rate)
        fresh.append(d)
        print_row(d)

    # 预热
    reset_hop()
    list(cosy.inference_zero_shot("你好。", "", REF_WAV, zero_shot_spk_id="elysia", stream=False))

    # A. 纯基线：不带任何指令，走官方缓存路径
    for i in range(2):
        reset_hop()
        save_and_measure(
            f"A{i+1}_纯基线_官方路径",
            list(cosy.inference_zero_shot(
                TEXT, "", REF_WAV, zero_shot_spk_id="elysia", stream=False)),
        )

    # B. 非缓存路径：每次重编码参考音频（排除缓存引入的问题）
    reset_hop()
    save_and_measure(
        "B_非缓存_重编码参考",
        list(cosy.inference_zero_shot(TEXT, ref_text, REF_WAV, zero_shot_spk_id="", stream=False)),
    )

    # C. 混合调用 F2（Phase 0 用的情绪路径）
    def hybrid(instruct_text: str):
        cached = dict(cosy.frontend.spk2info["elysia"])
        ttk, ttl = cosy.frontend._extract_text_token(TEXT)
        itk, itl = cosy.frontend._extract_text_token(instruct_text)
        mi = {
            "text": ttk, "text_len": ttl,
            "prompt_text": itk, "prompt_text_len": itl,
            "flow_prompt_speech_token": cached["flow_prompt_speech_token"],
            "flow_prompt_speech_token_len": cached["flow_prompt_speech_token_len"],
            "prompt_speech_feat": cached["prompt_speech_feat"],
            "prompt_speech_feat_len": cached["prompt_speech_feat_len"],
            "llm_embedding": cached["llm_embedding"],
            "flow_embedding": cached["flow_embedding"],
        }
        return list(cosy.model.tts(**mi, stream=False, speed=1.0))

    reset_hop()
    try:
        save_and_measure(
            "C_混合调用F2_开心",
            hybrid("You are a helpful assistant. 请非常开心地说一句话。<|endofprompt|>"),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  [C_混合调用F2_开心] 失败: {type(exc).__name__}: {exc}")

    # D. 用同一句参考文本作为合成文本（"复述"测试：最能反映克隆像不像）
    reset_hop()
    save_and_measure(
        "D_复述参考文本",
        list(cosy.inference_zero_shot(
            "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀",
            "", REF_WAV, zero_shot_spk_id="elysia", stream=False)),
    )

    # ---- 对比结论 ----
    print("\n" + "=" * 96)
    print(" 与参考音频的客观差距")
    print("=" * 96)
    print(f"{'样本':<26}{'F0中位':>9}{'F0差':>9}{'质心':>9}{'质心差':>9}{'HNR':>9}{'HNR差':>9}")
    print("-" * 96)

    def gap(d: dict) -> None:
        df0 = d["f0_median"] - ref_desc["f0_median"]
        dc = d["centroid"] - ref_desc["centroid"]
        dh = d["hnr_db"] - ref_desc["hnr_db"]
        flag = ""
        if abs(df0) > 40:
            flag += " ←F0偏离大"
        if abs(dc) > 600:
            flag += " ←音色偏离大"
        print(
            f"{d['name']:<26}{d['f0_median']:>9.1f}{df0:>+9.1f}"
            f"{d['centroid']:>9.0f}{dc:>+9.0f}{d['hnr_db']:>9.1f}{dh:>+9.1f}{flag}"
        )

    for d in existing:
        gap(d)
    for d in fresh:
        gap(d)

    print(f"\n输出目录: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
