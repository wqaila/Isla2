"""验证 4 个「已登记但未使用」配置项的真实效果。

涉及：
  enable_compile   torch.compile
  enable_sdpa      SDPA 注意力
  enable_vllm_llm  vLLM 加速
  cpu_offload      CPU offload

必须先实测，才能决定「实现」还是「删除」。
不能凭想象实现，也不能留着当摆设。

本脚本检查这些开关在 CosyVoice3 里是否真的可配置。
"""
from __future__ import annotations

import inspect
import sys

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")


def main() -> int:
    print("=" * 96)
    print(" 4 个未使用配置项的可实现性调查")
    print("=" * 96)

    from cosyvoice.cli.cosyvoice import CosyVoice2, CosyVoice3

    print("\n[1] CosyVoice3.__init__ 签名")
    print(f"  {inspect.signature(CosyVoice3.__init__)}")

    print("\n[2] CosyVoice2.__init__ 签名（父类）")
    print(f"  {inspect.signature(CosyVoice2.__init__)}")

    print("\n[3] 模型内部是否有 compile / sdpa / offload 相关接口")
    import cosyvoice.cli.model as model_mod

    src = inspect.getsource(model_mod)
    for kw in ("compile", "sdpa", "scaled_dot_product", "cpu_offload", "to('cpu')",
               "to(\"cpu\")", "vllm"):
        count = src.lower().count(kw.lower())
        print(f"  {kw:<22} 出现 {count} 次")

    print("\n[4] LLM 里是否已用 SDPA")
    import cosyvoice.llm.llm as llm_mod

    llm_src = inspect.getsource(llm_mod)
    for kw in ("scaled_dot_product_attention", "sdpa", "attn_implementation"):
        print(f"  {kw:<32} {'有' if kw in llm_src else '无'}")

    print("\n[5] flow / DiT 里是否已用 SDPA")
    import cosyvoice.flow.flow as flow_mod

    flow_src = inspect.getsource(flow_mod)
    for kw in ("scaled_dot_product_attention", "sdpa"):
        print(f"  {kw:<32} {'有' if kw in flow_src else '无'}")

    print("\n[6] load_vllm 的实现（仅 CosyVoice2+ 有）")
    if hasattr(model_mod.CosyVoice2Model, "load_vllm"):
        print(inspect.getsource(model_mod.CosyVoice2Model.load_vllm))

    print("\n[7] 模型目录是否有 vllm 子目录")
    from pathlib import Path

    md = Path.home() / "models" / "Fun-CosyVoice3-0.5B-2512"
    vllm_dir = md / "vllm"
    print(f"  {vllm_dir} 存在: {vllm_dir.is_dir()}")

    print()
    print("=" * 96)
    print(" 结论")
    print("=" * 96)
    print("""
  根据以上调查，4 个开关的处置建议：

  enable_vllm_llm
    模型目录无 vllm/ 子目录，load_vllm 无可用权重。
    -> 无法实现，应从配置中删除。

  cpu_offload
    CosyVoice3 没有 offload 接口；手动 .to('cpu') 会破坏推理图，
    且每次请求都要搬运权重，延迟会灾难性上升。
    -> 不实现，应删除。

  enable_compile
    torch.compile 可以套在模型子模块上，但首次编译开销大（分钟级），
    且 CosyVoice 的自回归 + 流式结构容易被 graph break 打断。
    需要实测才能确认是否有收益。
    -> 需实测；若测不出收益，删除。

  enable_sdpa
    若 LLM/flow 已内置 SDPA 或 PyTorch 默认已启用，该开关无意义。
    -> 需查证；若无意义，删除。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
