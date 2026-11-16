"""诊断：参考音频像角色，但合成结果不像 —— 音色在克隆环节损失在哪。

用户反馈：4 个候选参考都接近角色，但 synth_*.wav 反倒不像。

已有线索（Phase 0 数据）：
  参考音频   平坦度 0.2760  质心 2913Hz
  官方参考   平坦度 0.2150  质心 1934Hz
  我们合成   平坦度 0.2853  质心 3403Hz
  官方合成   平坦度 0.2090  质心 2175Hz

假设：
  H1 参考素材本身频谱平坦度偏高（噪声感），合成会放大这个特征
  H2 参考音频缺自然停顿（5.24% 纯零静音，语句间停顿被噪声门切除），
     导致模型学不到韵律 → 听起来机械
  H3 单段参考只覆盖一种语气，speaker embedding 不完整
  H4 HiFT 声码器在无 text_frontend 时韵律处理粗糙

本脚本量化 H1/H2/H3，并测不同参考长度/组合的影响。
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
SRC = Path("/mnt/c/Users/user/Desktop/aili")
OFFICIAL = Path("/home/user/CosyVoice/asset/zero_shot_prompt.wav")
REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
CAND = Path("/mnt/d/xiangmudaima/zjy14/_ref_select")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_clone_diag")
OUT.mkdir(parents=True, exist_ok=True)
SR = 24000


def frames(x, sr, win_s=0.04, hop_s=0.02):
    win, hop = int(sr * win_s), int(sr * hop_s)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return np.zeros((0, win))
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return x[idx].astype(np.float64)


def hnr(x, sr, fmin=70.0, fmax=450.0):
    """谐噪比：越高说明周期性（人声）成分越强，越低说明噪声/气声越多。"""
    fr = frames(x, sr)
    if fr.shape[0] == 0:
        return 0.0
    e = np.sqrt((fr**2).mean(axis=1))
    fr = fr[e >= np.percentile(e, 50)]
    lo, hi = int(sr / fmax), int(sr / fmin)
    vals = []
    for f in fr[:300]:
        f = f - f.mean()
        ac = np.correlate(f, f, mode="full")[len(f) - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:min(hi, len(ac))]
        if seg.size == 0:
            continue
        r = float(np.max(seg) / ac[0])
        if 0 < r < 1:
            vals.append(10 * np.log10(r / (1 - r)))
    return float(np.median(vals)) if vals else 0.0


def flatness(x, sr):
    fr = frames(x, sr)
    if fr.shape[0] == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(fr * np.hanning(fr.shape[1]), axis=1)) + 1e-12
    gm = np.exp(np.mean(np.log(spec), axis=1))
    am = np.mean(spec, axis=1)
    return float(np.median(gm / am))


def centroid(x, sr):
    fr = frames(x, sr)
    if fr.shape[0] == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(fr * np.hanning(fr.shape[1]), axis=1))
    freqs = np.fft.rfftfreq(fr.shape[1], 1.0 / sr)
    mag = spec.sum(axis=0)
    return float((mag * freqs).sum() / mag.sum()) if mag.sum() > 0 else 0.0


def pause_structure(x, sr):
    """分析停顿结构：静音段数量、平均时长、语音段数量。"""
    hop = int(sr * 0.02)
    n = len(x) // hop
    if n == 0:
        return {"pauses": 0, "pause_ratio": 0.0, "segments": 0,
                "mean_pause_ms": 0.0, "exact_zero_ratio": 0.0}
    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    # 纯数字零检测（噪声门痕迹）
    exact_zero = float(np.mean(rms < 1e-9))
    thr = max(float(np.percentile(rms, 15)) * 2, 1e-6)
    silent = rms < thr

    pauses, segs = [], 0
    i = 0
    while i < n:
        if silent[i]:
            j = i
            while j < n and silent[j]:
                j += 1
            pauses.append((j - i) * 0.02)
            i = j
        else:
            j = i
            while j < n and not silent[j]:
                j += 1
            segs += 1
            i = j

    # 忽略首尾（截取导致）
    inner = pauses[1:-1] if len(pauses) > 2 else []
    return {
        "pauses": len(inner),
        "pause_ratio": round(float(silent.mean()), 3),
        "segments": segs,
        "mean_pause_ms": round(float(np.mean(inner) * 1000), 0) if inner else 0.0,
        "exact_zero_ratio": round(exact_zero, 4),
    }


def describe(name, x, sr=SR):
    p = pause_structure(x, sr)
    return {
        "name": name,
        "dur": round(len(x) / sr, 2),
        "flat": round(flatness(x, sr), 4),
        "hnr": round(hnr(x, sr), 1),
        "centroid": round(centroid(x, sr), 0),
        **p,
    }


def print_row(d):
    print(
        f"  {d['name']:<30}{d['dur']:>6.2f}s  平坦{d['flat']:>7.4f}  "
        f"HNR{d['hnr']:>6.1f}dB  质心{d['centroid']:>6.0f}Hz  "
        f"停顿{d['pauses']:>2}个/{d['mean_pause_ms']:>5.0f}ms  "
        f"纯零{d['exact_zero_ratio']:>6.2%}"
    )


def main() -> int:
    print("=" * 110)
    print(" A. 参考音频的声学特征对比（平坦度/HNR/停顿结构）")
    print("=" * 110)

    off, _ = sf.read(str(OFFICIAL), dtype="float32")
    if off.ndim > 1:
        off = off.mean(axis=1)
    print_row(describe("官方示例参考", off))

    ref, _ = sf.read(str(REF), dtype="float32")
    if ref.ndim > 1:
        ref = ref.mean(axis=1)
    print_row(describe("当前参考(亢奋段)", ref))

    cands = {}
    for p in sorted(CAND.glob("cand_*.wav")):
        x, sr = sf.read(str(p), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        cands[p.stem.replace("cand_", "")] = (p, x)
        print_row(describe(p.stem.replace("cand_", ""), x, sr))

    print()
    print("  解读：")
    print("    平坦度接近 1 = 噪声样；接近 0 = 强谐波（乐音/浊音）")
    print("    HNR 越高 = 周期性越好 = 越像正常嗓音；低 = 气声/噪声多")
    print("    纯零 = 噪声门处理痕迹；停顿 = 自然呼吸间隙")

    print()
    print("=" * 110)
    print(" B. 合成结果 vs 参考：音色损失在哪一维")
    print("=" * 110)

    pairs = [
        ("官方参考->官方合成", OFFICIAL, Path("/mnt/d/xiangmudaima/zjy14/_ref_ab/官方1.wav")),
        ("当前参考->当前合成", REF, Path("/mnt/d/xiangmudaima/zjy14/_ref_ab/爱莉9.7s_1.wav")),
    ]
    print(f"  {'配对':<26}{'平坦度变化':>14}{'HNR变化':>12}{'质心变化':>12}")
    print("  " + "-" * 66)
    for label, ref_p, syn_p in pairs:
        if not syn_p.is_file():
            continue
        r, _ = sf.read(str(ref_p), dtype="float32")
        s, _ = sf.read(str(syn_p), dtype="float32")
        if r.ndim > 1:
            r = r.mean(axis=1)
        if s.ndim > 1:
            s = s.mean(axis=1)
        dr = describe("r", r)
        ds = describe("s", s)
        print(
            f"  {label:<26}{ds['flat'] - dr['flat']:>+14.4f}"
            f"{ds['hnr'] - dr['hnr']:>+12.1f}dB{ds['centroid'] - dr['centroid']:>+12.0f}Hz"
        )

    print()
    print("=" * 110)
    print(" C. 实验：参考音频的不同处理方式对合成音色的影响")
    print("=" * 110)

    from cosyvoice.cli.cosyvoice import CosyVoice3

    TEXT = "今天天气真不错，我们一起出去走走吧。"
    PREFIX = "You are a helpful assistant.<|endofprompt|>"

    # 准备变体
    variants = []

    # C1 原始
    variants.append(("C1_原始9.7s", REF, "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀"))

    # C2 去掉首尾静音（让语音更紧凑）
    hop = int(SR * 0.02)
    n = len(ref) // hop
    rms = np.sqrt((ref[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    voiced_idx = np.where(rms > np.percentile(rms, 15) * 2)[0]
    if voiced_idx.size:
        a, b = voiced_idx[0] * hop, min((voiced_idx[-1] + 1) * hop, len(ref))
        trimmed = ref[a:b]
        p = OUT / "c2_trimmed.wav"
        sf.write(str(p), trimmed, SR, subtype="PCM_16")
        variants.append(("C2_去首尾静音", p, None))

    # C3 峰值归一化到 -3dBFS
    peak = float(np.max(np.abs(ref)))
    if peak > 0:
        norm = (ref * (0.708 / peak)).astype(np.float32)
        p = OUT / "c3_normalized.wav"
        sf.write(str(p), norm, SR, subtype="PCM_16")
        variants.append(("C3_峰值归一化", p, None))

    # C4 短一点（前 5 秒）
    p = OUT / "c4_5s.wav"
    sf.write(str(p), ref[: int(5 * SR)], SR, subtype="PCM_16")
    variants.append(("C4_前5秒", p, None))

    cosy = CosyVoice3(MODEL, fp16=False)
    print(f"  text_frontend = {cosy.frontend.text_frontend!r}")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    results = []
    for label, wav_path, txt in variants:
        # 文本：C2/C3/C4 是同一段的变体，用同一文本；C4 只取前5秒需谨慎
        if txt is None:
            if "C4" in label:
                continue  # 文本对不齐，跳过避免误判
            txt = "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡这可是身为副首领最重要的职责呀"
        prompt = txt if "<|endofprompt|>" in txt else PREFIX + txt
        reset_hop()
        try:
            chunks = list(cosy.inference_zero_shot(
                TEXT, prompt, str(wav_path), zero_shot_spk_id="", stream=False))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{label}] 失败: {type(exc).__name__}: {exc}")
            continue
        if not chunks:
            continue
        y = chunks[0]["tts_speech"].detach().cpu().float().numpy().squeeze()
        sf.write(str(OUT / f"synth_{label}.wav"), y, SR, subtype="PCM_16")
        d = describe(label, y)
        results.append(d)
        print_row(d)

    print()
    print("=" * 110)
    print(" D. 用不同候选参考合成，看谁最接近「参考本身的音色」")
    print("=" * 110)

    # 候选的文本（key 与 cands 的 key 对齐：已去掉 .mp3 后缀）
    cand_texts = {
        "1.0": "早上好，新的一天从一场美妙的邂逅开始",
        "1.4": "这么晚了还不睡吗，是在想我对不对",
        "1.5": "睡不着的话就来聊聊天吧",
        "1.6": "晚上好，如此美妙的夜晚",
    }
    for key, (p, x) in cands.items():
        txt = cand_texts.get(key)
        if not txt:
            continue
        prompt = PREFIX + txt
        reset_hop()
        try:
            chunks = list(cosy.inference_zero_shot(
                TEXT, prompt, str(p), zero_shot_spk_id="", stream=False))
        except Exception as exc:  # noqa: BLE001
            print(f"  [{key}] 失败: {type(exc).__name__}: {exc}")
            continue
        if not chunks:
            continue
        y = chunks[0]["tts_speech"].detach().cpu().float().numpy().squeeze()
        sf.write(str(OUT / f"synth_{key}.wav"), y, SR, subtype="PCM_16")
        dr = describe(f"参考_{key}", x)
        ds = describe(f"合成_{key}", y)
        results.append(ds)
        print_row(dr)
        print_row(ds)
        print(
            f"      差值: 平坦度 {ds['flat']-dr['flat']:+.4f}  "
            f"HNR {ds['hnr']-dr['hnr']:+.1f}dB  质心 {ds['centroid']-dr['centroid']:+.0f}Hz"
        )
        print()

    print("=" * 110)
    print(" 汇总：合成结果质量排名（HNR 越高、平坦度越低越好）")
    print("=" * 110)
    print(f"  {'样本':<30}{'平坦度':>9}{'HNR':>9}{'质心':>9}")
    print("  " + "-" * 58)
    for d in sorted(results, key=lambda d: (-d["hnr"], d["flat"])):
        print(f"  {d['name']:<30}{d['flat']:>9.4f}{d['hnr']:>8.1f}dB{d['centroid']:>8.0f}Hz")

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
