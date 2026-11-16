"""用 FunASR 回读短片，验证文本标注是否残缺。

这是决定性验证：
  若 ASR 识别出的文本显著长于标注文本，说明标注不全，
  参考音频与 prompt_text 不对齐，直接导致合成韵律失真。

运行环境：zjy13/.venv-asr（有 FunASR）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

CAND = Path("/mnt/d/xiangmudaima/zjy14/_ref_select")
MODEL_DIR = Path("/mnt/d/xiangmudaima/zjy13/_models")

CLAIMS = {
    "1.0": "早上好，新的一天从一场美妙的邂逅开始",
    "1.1": "这么早起床是为了能早点见到我吗",
    "1.3": "快去休息了，约好了我们在梦里相见",
    "1.4": "这么晚了还不睡吗，是在想我对不对",
    "1.5": "睡不着的话就来聊聊天吧",
    "1.6": "晚上好，如此美妙的夜晚",
    "1.7": "月亮升起来了，要陪我一起去看星星吗",
}


def main() -> int:
    import os

    os.environ.setdefault("MODELSCOPE_CACHE", str(MODEL_DIR))

    try:
        from funasr import AutoModel
    except ImportError as exc:
        print(f"[错误] FunASR 未安装: {exc}")
        print("请在 zjy13/.venv-asr 环境运行")
        return 1

    model_path = MODEL_DIR / "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
    print(f"加载 ASR 模型: {model_path}")
    model = AutoModel(
        model=str(model_path) if model_path.is_dir() else "paraformer-zh",
        disable_update=True,
        device="cpu",
    )

    print()
    print("=" * 100)
    print(" ASR 回读 vs 标注文本")
    print("=" * 100)

    for key, claim in CLAIMS.items():
        p = CAND / f"cand_{key}.wav"
        if not p.is_file():
            continue
        res = model.generate(input=str(p), batch_size_s=300)
        text = res[0]["text"] if res else ""
        # 去掉 FunASR 的标点标记
        clean = text.replace("<|zh|>", "").replace("<|NEUTRAL|>", "")
        clean = clean.replace("<|Speech|>", "").replace("<|/Speech|>", "")
        clean = clean.strip()

        n_claim = len(claim.replace("，", ""))
        n_asr = len(clean.replace("，", "").replace("。", "").replace(" ", ""))

        print(f"\n  [{key}] {p.name}")
        print(f"    标注: {claim}")
        print(f"          {n_claim} 字符")
        print(f"    ASR : {clean}")
        print(f"          {n_asr} 字符")
        if n_asr > n_claim * 1.3:
            print(f"    >>> ASR 多出 {n_asr - n_claim} 字符，文本标注不完整！")
        elif n_asr < n_claim * 0.7:
            print(f"    >>> ASR 少了 {n_claim - n_asr} 字符，可能识别不准")
        else:
            print(f"    基本一致（差异 {abs(n_asr - n_claim)} 字符）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
