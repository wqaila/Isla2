"""Phase 2b 前置验证：确认哪些控制维度真正有效。

D1 已证明情绪指令无效。路线乙依赖三个维度：
  speed  语速
  volume 音量（音频层增益）
  pause  停顿（文本标点/分句）

本脚本量化前两个在模型层的作用，第三个在文本层的作用。

关键问题：
  Q1 speed 参数的实际效果范围（0.8/0.9/1.0/1.1/1.2 分别对应多少时长变化）
  Q2 speed 是否影响音色（如果改变音色，就不能自由用）
  Q3 标点/换行能否影响停顿（如果模型不响应标点，停顿控制就无效）
  Q4 分句合成 vs 整段合成，哪种更可控
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")
sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
REF_WAV = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
REF_TXT = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_p2b_speed")
OUT.mkdir(parents=True, exist_ok=True)

SR = 24000
PREFIX = "You are a helpful assistant.<|endofprompt|>"

# 用于 speed 测试的固定文本
TEXT = "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。"

# 用于停顿测试的三组文本
PAUSE_TEXTS = {
    "无标点": "今天天气真不错我们一起出去走走吧",
    "逗号": "今天天气真不错，我们一起出去走走吧",
    "句号分隔": "今天天气真不错。我们一起出去走走吧。",
    "长停顿": "今天天气真不错。。我们一起出去走走吧",   # 双句号
    "换行": "今天天气真不错\n\n我们一起出去走走吧",
    "省略号": "今天天气真不错……我们一起出去走走吧",
}

SPEEDS = [0.8, 0.9, 1.0, 1.1, 1.2]


def count_chars(t: str) -> int:
    return len([c for c in t if c not in "，。！？、；：,.!?;: \n\t"])


def voiced(x: np.ndarray, sr: int) -> tuple[float, float, int]:
    """返回 (有声时长, 静音占比, 停顿段数>150ms)。"""
    hop = int(sr * 0.01)
    n = len(x) // hop
    if n == 0:
        return 0.0, 1.0, 0
    rms = np.sqrt((x[: n * hop].reshape(n, hop).astype(np.float64) ** 2).mean(axis=1))
    pk = float(rms.max())
    if pk <= 0:
        return 0.0, 1.0, 0
    v = rms > max(pk * 0.02, 1e-6)
    idx = np.where(v)[0]
    if idx.size == 0:
        return 0.0, 1.0, 0
    inner = v[idx[0]: idx[-1] + 1]

    pauses = 0
    i = 0
    while i < len(inner):
        if not inner[i]:
            j = i
            while j < len(inner) and not inner[j]:
                j += 1
            if (j - i) * 0.01 >= 0.15:
                pauses += 1
            i = j
        else:
            i += 1

    return float(inner.sum()) * 0.01, float((~inner).mean()), pauses


def f0(x: np.ndarray, sr: int, fmin=70.0, fmax=450.0) -> float:
    win, hop = int(sr * 0.04), int(sr * 0.01)
    n = max(0, (len(x) - win) // hop + 1)
    if n <= 0:
        return 0.0
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
    e = np.sqrt((fr**2).mean(axis=1))
    fr = fr[e >= np.percentile(e, 70)]
    if fr.shape[0] == 0:
        return 0.0
    lo, hi = int(sr / fmax), int(sr / fmin)
    out = []
    for f in fr:
        f = f - f.mean()
        ac = np.correlate(f, f, mode="full")[len(f) - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:min(hi + 1, len(ac))]
        if seg.size == 0:
            continue
        pk = int(np.argmax(seg)) + lo
        if ac[pk] < 0.3 * ac[0]:
            continue
        out.append(sr / pk)
    return float(np.median(out)) if out else 0.0


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3
    from custom_voice.inference.text_frontend import enable_wetext_frontend

    ref_body = REF_TXT.read_text(encoding="utf-8").strip()
    if "<|endofprompt|>" in ref_body:
        ref_body = ref_body.split("<|endofprompt|>", 1)[1]
    ref_full = PREFIX + ref_body

    print("=" * 100)
    print(" Phase 2b 前置验证：哪些控制维度真正有效")
    print("=" * 100)

    cosy = CosyVoice3(str(MODEL), fp16=False)
    enable_wetext_frontend(cosy.frontend)
    cosy.add_zero_shot_spk(ref_full, str(REF_WAV), "elysia")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    reset_hop()
    list(cosy.inference_zero_shot("你好。", "", str(REF_WAV),
                                  zero_shot_spk_id="elysia", stream=False))

    nc = count_chars(TEXT)

    # ---- Q1/Q2：speed 效果 ----
    print()
    print("-" * 100)
    print(" Q1/Q2：speed 参数的效果与副作用（每种 2 次采样）")
    print("-" * 100)
    print(f"  {'speed':>7}{'时长':>9}{'语速':>9}{'F0':>9}  相对 1.0")
    print("  " + "-" * 56)

    speed_rows = []
    for sp in SPEEDS:
        durs, rates, f0s = [], [], []
        for _ in range(2):
            reset_hop()
            try:
                chunks = list(cosy.inference_zero_shot(
                    TEXT, "", str(REF_WAV), zero_shot_spk_id="elysia",
                    stream=False, speed=sp))
            except Exception as exc:  # noqa: BLE001
                print(f"  {sp:>7} 失败: {type(exc).__name__}")
                break
            if not chunks:
                continue
            parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                     for c in chunks]
            y = np.concatenate(parts) if len(parts) > 1 else parts[0]
            sf.write(str(OUT / f"speed_{sp}_{len(durs)+1}.wav"), y, SR,
                     subtype="PCM_16")
            dur = len(y) / SR
            vd, _, _ = voiced(y, SR)
            durs.append(dur)
            rates.append(nc / max(vd, 0.01))
            f0s.append(f0(y, SR))

        if durs:
            med_dur = float(np.median(durs))
            med_rate = float(np.median(rates))
            med_f0 = float(np.median(f0s))
            speed_rows.append((sp, med_dur, med_rate, med_f0))

    base = next((r for r in speed_rows if abs(r[0] - 1.0) < 1e-6), None)
    for sp, dur, rate, mf0 in speed_rows:
        rel = f"{(dur/base[1] - 1)*100:+.1f}%" if base else ""
        print(f"  {sp:>7.1f}{dur:>8.2f}s{rate:>8.2f}{mf0:>9.1f}  {rel}")

    # ---- Q3：标点是否影响停顿 ----
    print()
    print("-" * 100)
    print(" Q3：标点/换行能否控制停顿")
    print("-" * 100)
    print(f"  {'写法':<14}{'时长':>9}{'静音%':>8}{'停顿数':>8}  说明")
    print("  " + "-" * 60)

    pause_rows = []
    for label, text in PAUSE_TEXTS.items():
        reset_hop()
        try:
            chunks = list(cosy.inference_zero_shot(
                text, "", str(REF_WAV), zero_shot_spk_id="elysia", stream=False))
        except Exception as exc:  # noqa: BLE001
            print(f"  {label:<14} 失败: {type(exc).__name__}: {str(exc)[:40]}")
            continue
        if not chunks:
            continue
        parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                 for c in chunks]
        y = np.concatenate(parts) if len(parts) > 1 else parts[0]
        sf.write(str(OUT / f"pause_{label}.wav"), y, SR, subtype="PCM_16")
        dur = len(y) / SR
        vd, sil, npause = voiced(y, SR)
        pause_rows.append((label, dur, sil, npause, len(chunks)))
        print(f"  {label:<14}{dur:>8.2f}s{sil:>8.1%}{npause:>8}  "
              f"分 {len(chunks)} 段")

    # ---- Q4：分句合成 vs 整段 ----
    print()
    print("-" * 100)
    print(" Q4：分句合成（手动切分 + 拼接）vs 整段合成")
    print("-" * 100)

    segments = ["今天天气真不错，", "我们一起出去走走吧。", "昨天下了整整一天的雨。"]
    reset_hop()
    whole = list(cosy.inference_zero_shot(
        "".join(segments), "", str(REF_WAV), zero_shot_spk_id="elysia", stream=False))
    if whole:
        y = whole[0]["tts_speech"].detach().cpu().float().numpy().squeeze()
        sf.write(str(OUT / "seg_整段.wav"), y, SR, subtype="PCM_16")
        vd, sil, npause = voiced(y, SR)
        print(f"  {'整段合成':<14}{len(y)/SR:>8.2f}s{sil:>8.1%}{npause:>8}  "
              f"分 {len(whole)} 段")

    pieces = []
    gap = np.zeros(int(0.25 * SR), dtype=np.float32)  # 手动插入 250ms 静音
    for i, seg in enumerate(segments):
        reset_hop()
        ch = list(cosy.inference_zero_shot(
            seg, "", str(REF_WAV), zero_shot_spk_id="elysia", stream=False))
        if not ch:
            continue
        y = ch[0]["tts_speech"].detach().cpu().float().numpy().squeeze()
        pieces.append(y.astype(np.float32))
    if pieces:
        merged = []
        for i, p in enumerate(pieces):
            if i > 0:
                merged.append(gap)
            merged.append(p)
        y2 = np.concatenate(merged)
        sf.write(str(OUT / "seg_分句拼接.wav"), y2, SR, subtype="PCM_16")
        vd, sil, npause = voiced(y2, SR)
        print(f"  {'分句拼接(250ms)':<14}{len(y2)/SR:>8.2f}s{sil:>8.1%}"
              f"{npause:>8}  分 {len(pieces)} 段")

    # ---- 结论 ----
    print()
    print("=" * 100)
    print(" 结论")
    print("=" * 100)

    if len(speed_rows) >= 2:
        lo = min(speed_rows, key=lambda r: r[1])
        hi = max(speed_rows, key=lambda r: r[1])
        print(f"  Q1 speed 效果：")
        print(f"     最快 {lo[0]} -> {lo[1]:.2f}s")
        print(f"     最慢 {hi[0]} -> {hi[1]:.2f}s")
        print(f"     时长跨度 {(hi[1]/lo[1] - 1)*100:.0f}%，"
              f"{'可用' if (hi[1]/lo[1] - 1) > 0.2 else '效果有限'}")

        f0_range = max(r[3] for r in speed_rows) - min(r[3] for r in speed_rows)
        print(f"  Q2 副作用：F0 跨度 {f0_range:.1f}Hz "
              f"{'（音色稳定，可自由使用）' if f0_range < 15 else '（音色有变化，需谨慎）'}")

    if pause_rows:
        sil_vals = [r[2] for r in pause_rows]
        print(f"  Q3 标点控制停顿：静音占比 {min(sil_vals):.1%} ~ {max(sil_vals):.1%}")
        if max(sil_vals) - min(sil_vals) > 0.05:
            print("     标点有影响，可用于停顿控制")
        else:
            print("     标点影响很小，停顿需靠分句拼接实现")

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
