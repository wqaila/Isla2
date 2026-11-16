"""验证：能否阻止上游 CosyVoice3 初始化时的 wetext 联网下载。

问题（实测）：
  CosyVoice3.__init__ -> CosyVoiceFrontEnd.__init__ 里会 try import wetext，
  wetext 的 __init__ 无条件调 snapshot_download()。
  无 token 时会先下载部分文件（很慢，实测 90s+ 才失败），
  导致服务启动被拖住。

  我的 enable_wetext_frontend 补丁在 CosyVoice3 构造**之后**才调用，
  所以阻止不了这次下载。

解决方向：
  A. 设置环境变量 MODELSCOPE_OFFLINE=1 让 snapshot_download 直接失败
  B. 设置 MODELSCOPE_CACHE 指向不存在的目录 + 离线模式
  C. monkey-patch modelscope.snapshot_download 在构造前替换成抛异常

本脚本验证 A/B/C 哪个有效且不破坏后续流程。
"""
from __future__ import annotations

import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")
sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")


def try_patch_approach() -> None:
    """方案 C：构造前把 snapshot_download 换成抛异常。"""
    print("=" * 90)
    print(" 方案 C：monkey-patch modelscope.snapshot_download")
    print("=" * 90)

    import modelscope

    original = modelscope.snapshot_download

    def _fail(*args, **kwargs):
        raise RuntimeError("blocked by zjy14: 禁止启动期联网下载")

    modelscope.snapshot_download = _fail
    try:
        t0 = time.time()
        from cosyvoice.cli.cosyvoice import CosyVoice3

        cosy = CosyVoice3(str(MODEL), fp16=False)
        elapsed = time.time() - t0
        print(f"  构造耗时 {elapsed:.1f}s")
        print(f"  text_frontend = {cosy.frontend.text_frontend!r}")
        print("  [OK] 构造成功，未被下载阻塞")

        # 再启用我们的本地补丁
        from custom_voice.inference.text_frontend import enable_wetext_frontend

        enable_wetext_frontend(cosy.frontend)
        print(f"  启用补丁后 text_frontend = {cosy.frontend.text_frontend!r}")
        print("  [OK] 本地 fst 补丁生效")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {type(exc).__name__}: {exc}")
    finally:
        modelscope.snapshot_download = original


def main() -> int:
    try_patch_approach()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
