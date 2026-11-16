"""验证 TN 的真正价值：数字是否被正确朗读。

方法（不依赖听感）：
  若 TN 生效，"2026年11月9日" 应该被读成 "二零二六年十一月九日"，
  两者合成的音频时长应该接近（因为读的是同一串音节）。

  若 TN 未生效，模型直接看到 "2026" 这几个字符，
  可能读成 "二〇二六" 也可能读错或跳过，时长会明显不同。

对照组：
  A  数字写法    "现在是2026年11月9日"        （TN 会转成中文）
  B  中文写法    "现在是二零二六年十一月九日"   （TN 无需转换）
  若 A≈B，说明 TN 生效且正确。

同时测采样稳定性（同输入多次合成，看指标波动幅度），
用来判断之前的差异是 TN 效果还是采样噪声。
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

from custom_voice.inference.text_frontend import enable_wetext_frontend  # noqa: E402

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
REF = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
OUT = Path("/mnt/d/xiangmudaima/zjy14/_tn_verify")
OUT.mkdir(parents=True, exist_ok=True)
SR = 24000

REF_TEXT = (
    "You are a helpful assistant.<|endofprompt|>"
    "我一直都在努力维持优雅美丽可爱这三者之间恰到好处的平衡"
    "这可是身为副首领最重要的职责呀"
)

# A/B 对照：数字写法 vs 中文写法
TEXT_DIGITS = "现在是2026年11月9日，温度25度。"
TEXT_CHINESE = "现在是二零二六年十一月九日，温度二十五度。"


def syllable_cv(x, sr):
    win, hop = int(sr * 0.04), int(sr * 0.01)
    n = max(0, (len(x) - win) // hop + 1)
    if n < 10:
        return 0.0, 0
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
    e = np.sqrt((fr**2).mean(axis=1))
    peak = float(e.max())
    if peak <= 0:
        return 0.0, 0
    voiced = e > max(peak * 0.05, 1e-6)
    vi = np.where(voiced)[0]
    if vi.size == 0:
        return 0.0, 0
    seg = voiced[vi[0]: vi[-1] + 1]
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
        return 0.0, len(lengths)
    arr = np.array(lengths)
    return float(arr.std() / max(arr.mean(), 1e-9)), len(lengths)


def main() -> int:
    from cosyvoice.cli.cosyvoice import CosyVoice3

    print("=" * 100)
    print(" 验证 1：TN 是否让数字被正确朗读（A/B 时长对照）")
    print("=" * 100)

    cosy = CosyVoice3(MODEL, fp16=False)
    enable_wetext_frontend(cosy.frontend)
    print(f"  text_frontend = {cosy.frontend.text_frontend!r}")
    cosy.add_zero_shot_spk(REF_TEXT, str(REF), "elysia")

    # 直接验证 TN 转换结果
    print("\n  TN 转换验证（直接调 frontend 的文本规范化）:")
    for label, t in (("数字写法", TEXT_DIGITS), ("中文写法", TEXT_CHINESE)):
        try:
            out = cosy.frontend.text_normalize(t, split=False, text_frontend=True)
            print(f"    {label}: {t}")
            print(f"      -> {out}")
        except Exception as exc:  # noqa: BLE001
            print(f"    {label} 失败: {type(exc).__name__}: {exc}")

    def reset_hop():
        if hasattr(cosy.model, "token_hop_len"):
            cosy.model.token_hop_len = 25

    def synth(text: str, tag: str) -> dict | None:
        reset_hop()
        try:
            chunks = list(cosy.inference_zero_shot(
                text, "", str(REF), zero_shot_spk_id="elysia", stream=False))
        except Exception as exc:  # noqa: BLE001
            print(f"    [{tag}] 失败: {type(exc).__name__}: {exc}")
            return None
        if not chunks:
            return None
        parts = [c["tts_speech"].detach().cpu().float().numpy().squeeze()
                 for c in chunks]
        y = np.concatenate(parts) if len(parts) > 1 else parts[0]
        sf.write(str(OUT / f"{tag}.wav"), y, SR, subtype="PCM_16")
        cv, n = syllable_cv(y, SR)
        return {"tag": tag, "dur": len(y) / SR, "cv": cv, "n": n}

    # 预热
    reset_hop()
    list(cosy.inference_zero_shot("你好。", "", str(REF), zero_shot_spk_id="elysia", stream=False))

    print()
    print("  A/B 合成对照（各 2 次，看稳定性）:")
    a_list, b_list = [], []
    for i in range(2):
        r = synth(TEXT_DIGITS, f"A_数字{i+1}")
        if r:
            a_list.append(r)
            print(f"    A_数字{i+1}   时长 {r['dur']:.2f}s  音节CV {r['cv']:.3f}({r['n']})")
    for i in range(2):
        r = synth(TEXT_CHINESE, f"B_中文{i+1}")
        if r:
            b_list.append(r)
            print(f"    B_中文{i+1}   时长 {r['dur']:.2f}s  音节CV {r['cv']:.3f}({r['n']})")

    if a_list and b_list:
        a_dur = float(np.mean([r["dur"] for r in a_list]))
        b_dur = float(np.mean([r["dur"] for r in b_list]))
        diff = abs(a_dur - b_dur)
        rel = diff / max(b_dur, 1e-9)
        print()
        print(f"  A(数字) 平均时长 {a_dur:.2f}s")
        print(f"  B(中文) 平均时长 {b_dur:.2f}s")
        print(f"  差异 {diff:.2f}s ({rel:.1%})")
        if rel < 0.12:
            print("  >>> 时长接近，说明数字被正确朗读为中文（TN 生效）")
        else:
            print("  >>> 时长差异较大，可能朗读方式不同（需人工确认）")

    print()
    print("=" * 100)
    print(" 验证 2：采样随机性有多大（判断之前的差异是否可信）")
    print("=" * 100)

    # 同一输入重复 4 次，看指标波动
    print("  同一文本重复 4 次（纯中文，TN 不改变任何字符）:")
    plain = "今天天气真不错，我们一起出去走走吧。昨天下了整整一天的雨。"
    stats = []
    for i in range(4):
        r = synth(plain, f"C_重复{i+1}")
        if r:
            stats.append(r)
            print(f"    第{i+1}次  时长 {r['dur']:.2f}s  音节CV {r['cv']:.3f}({r['n']})")

    if len(stats) >= 2:
        durs = [r["dur"] for r in stats]
        cvs = [r["cv"] for r in stats]
        print()
        print(f"  时长: 均值 {np.mean(durs):.2f}s  范围 {min(durs):.2f}-{max(durs):.2f}s  "
              f"标准差 {np.std(durs):.2f}s")
        print(f"  音节CV: 均值 {np.mean(cvs):.3f}  范围 {min(cvs):.3f}-{max(cvs):.3f}  "
              f"标准差 {np.std(cvs):.3f}")
        print()
        print("  若音节CV标准差 > 0.15，说明该指标噪声太大，")
        print("  之前 ON/OFF 的 CV 差异（0.623 vs 0.313）不可信。")

    print(f"\n输出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
