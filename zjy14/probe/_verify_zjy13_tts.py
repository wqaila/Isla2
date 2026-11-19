"""验证 zjy13 的 TTSClient 真的把请求发到了 zjy14。

为什么需要这个脚本
==================
``probe/_p8_device_check.py`` 的 [6] 项是**直接**打 zjy14 的 HTTP 接口，
绕过了 zjy13 自己的客户端代码。也就是说它只能证明"zjy14 服务本身能用"，
不能证明"板子听到的声音来自 zjy14"。

真正决定板子听什么的是 zjy13 里的 ``server/tts/client.py::TTSClient``
（``ws/server.py`` 每轮对话都调用它）。本脚本改用**那份生产代码**走一遍，
从而证明：

  1. zjy13 的 .env 里 TTS_BASE_URL 确实指向 zjy14；
  2. TTSClient 用这个地址能拿到 cosyvoice3 引擎（而不是 zjy13 旧引擎）；
  3. 非流式与流式两条路径都能产出音频。

用法
====
zjy13 的依赖装在它自己的虚拟环境里，所以要用 zjy13 的 python：

  cd D:\\xiangmudaima\\zjy13
  .\\venv-asr\\Scripts\\python.exe ..\\zjy14\\probe\\_verify_zjy13_tts.py

或在 WSL 里（若 zjy13 的依赖装在 WSL）：

  cd /mnt/d/xiangmudaima/zjy13 && python /mnt/d/xiangmudaima/zjy14/probe/_verify_zjy13_tts.py

退出码 0 = 板子下一轮对话走的是 zjy14 引擎。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

#: zjy13 的仓库根。默认与 zjy14 同级；可用环境变量覆盖。
import os

ZJY13_ROOT = Path(os.environ.get("ZJY13_ROOT", "/mnt/d/xiangmudaima/zjy13"))
if not ZJY13_ROOT.is_dir():
    # Windows 路径兜底（本脚本可能直接在 Windows 侧跑）
    alt = Path(r"D:\xiangmudaima\zjy13")
    if alt.is_dir():
        ZJY13_ROOT = alt

sys.path.insert(0, str(ZJY13_ROOT))

from server.config import load_env_file  # noqa: E402
from server.tts.client import TTSClient, TTSConfig  # noqa: E402

#: zjy14 的引擎端口。zjy13 旧引擎默认 50000。
ZJY14_TTS_PORT = 50001


async def run() -> int:
    print("=" * 70)
    print(" zjy13 TTSClient → zjy14 验证")
    print("=" * 70)
    print(f"  zjy13 根目录 = {ZJY13_ROOT}")

    # 必须显式加载 .env：TTSConfig.from_env() 只读 os.environ，不会读文件。
    # WS 服务进程是靠 server.ws.server 启动时调 load_env_file() 才有变量的，
    # 这里保持一致，否则会误报"未指向 zjy14"。
    load_env_file(ZJY13_ROOT / ".env")

    cfg = TTSConfig.from_env()
    print(f"  base_url = {cfg.base_url}")
    print(f"  voice    = {cfg.default_voice}")
    print(f"  stream   = {cfg.stream_url}")
    print()

    if f":{ZJY14_TTS_PORT}" not in cfg.base_url:
        print(f"  [FAIL] TTS 未指向 zjy14（期望 :{ZJY14_TTS_PORT}，"
              f"实际 {cfg.base_url}）")
        print("         改 zjy13/.env 的 TTS_BASE_URL 后**必须重启 zjy13 WS 服务**，")
        print("         因为环境变量是进程启动时读入的。")
        return 1

    client = TTSClient(cfg)
    try:
        # ---- 1) 健康检查：确认对面是 cosyvoice3 ----
        ok, detail = await client.health()
        print(f"  [1] health() = {ok}")
        if isinstance(detail, dict):
            print(f"      engine={detail.get('engine')}  "
                  f"device={detail.get('device')}  "
                  f"sample_rate={detail.get('sample_rate')}")
            engine = detail.get("engine")
            if engine != "cosyvoice3":
                print(f"  [FAIL] 对面引擎是 {engine!r}，不是 zjy14 的 cosyvoice3")
                return 1
        if not ok:
            print(f"  [FAIL] zjy14 健康检查失败: {detail}")
            return 1

        text = "你好，我是爱莉希雅，现在由 zjy14 的新引擎为我发声。"

        # ---- 2) 非流式 ----
        print(f"\n  [2] synthesize() 文本 {len(text)} 字 ...")
        t0 = time.perf_counter()
        result = await client.synthesize(text, voice_id=cfg.default_voice)
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"      音频 {result.duration_s:.2f}s  合成 {elapsed:.0f}ms  "
              f"RTF {result.realtime_factor:.3f}")
        print(f"      文件 {result.audio_path}")
        if result.duration_s <= 0:
            print("  [FAIL] 合成音频长度为 0")
            return 1
        if not Path(result.audio_path).is_file():
            print("  [FAIL] 音频文件未落盘")
            return 1
        print(f"      大小 {Path(result.audio_path).stat().st_size} 字节")

        # ---- 3) 流式（板子实际走这条路径）----
        print(f"\n  [3] synthesize_stream() 文本 {len(text)} 字 ...")
        t0 = time.perf_counter()
        chunks = 0
        total_bytes = 0
        first_ms = None
        async for pcm in client.synthesize_stream(text, voice_id=cfg.default_voice):
            if first_ms is None:
                first_ms = (time.perf_counter() - t0) * 1000
            chunks += 1
            total_bytes += len(pcm)
        total_ms = (time.perf_counter() - t0) * 1000
        if chunks == 0:
            print("  [FAIL] 流式合成没有返回任何音频块")
            return 1
        print(f"      块数 {chunks}  总字节 {total_bytes}  "
              f"首块 {first_ms:.0f}ms  全部 {total_ms:.0f}ms")
    finally:
        await client.aclose()

    print()
    print("=" * 70)
    print("  [OK] zjy13 的 TTS 客户端已成功调用 zjy14 引擎并产出音频。")
    print("       板子下一轮对话听到的就是这个引擎的声音。")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
