"""Phase 5 端到端验证：真模型流式 → 协议帧 → 模拟设备消费。

验证链路：
  CosyVoice3 引擎（真模型，流式）
    → bridge.stream_to_protocol_frames（切成 12 字节头协议帧）
    → FakeDevice（模拟固件：200ms 预缓冲 + seq 校验 + 结束标记）

要回答：
  Q1 真模型流式输出能否被正确切成协议帧
  Q2 首帧是否满足 300ms 首突发要求（让固件攒满预缓冲立刻开播）
  Q3 模拟设备能否正常开播、收尾、无 seq 跳变
  Q4 背压逻辑在真实音频上表现如何
  Q5 总字节数是否与引擎输出的音频时长一致（无丢帧/加帧）
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, "/home/user/CosyVoice")
sys.path.insert(0, "/home/user/CosyVoice/third_party/Matcha-TTS")
sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

MODEL = Path("/home/user/models/Fun-CosyVoice3-0.5B-2512")
REF_WAV = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.wav")
REF_TXT = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices/elysia/ref_elysia.txt")

# 覆盖短/中/长三档，验证路由与分帧
TEXTS = {
    "短句_10字": "你好呀，今天过得怎么样？",
    "中句_22字": "今天天气不错，我们下午去公园散步吧，顺便买点吃的。",
    "长句_44字": (
        "我最近在调试一块 ESP32-S3 的开发板，屏幕驱动那边一直有点问题，"
        "你有空的话能不能帮我看看？"
    ),
}


def main() -> int:
    from custom_voice.bridge.protocol import (
        BYTES_PER_SEC,
        DEFAULT_FRAME_BYTES,
        FIRST_BURST_MS,
        decode_binary_frame,
        describe_compat,
        encode_binary_frame,
        iter_frames,
    )
    from custom_voice.bridge.stream import (
        FakeDevice,
        stream_to_protocol_frames,
    )
    from custom_voice.config.loader import load_config
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine
    from custom_voice.inference.streaming import StreamPolicy, describe_routing
    from custom_voice.schemas.synthesis import SynthesisRequest

    print("=" * 100)
    print(" Phase 5 端到端：真模型流式 → 协议帧 → 模拟设备")
    print("=" * 100)

    compat = describe_compat()
    print(f"\n协议契约: magic={compat['binary_magic']} "
          f"header={compat['binary_header_size']}B "
          f"frame={compat['frame_bytes']}B({compat['frame_ms']}ms) "
          f"sr={compat['sample_rate']} 首突发={compat['first_burst_ms']}ms")

    # 加载真模型
    cfg = load_config(
        overrides={
            "engine": "cosyvoice3",
            "device": "cuda",
            "text_frontend": "auto",
            "memory_safety_margin_mb": 0,
        }
    )
    print("\n加载模型...")
    engine = CosyVoiceEngine(cfg)
    engine.load()
    print(f"  就绪（加载 {engine.stats().load_s:.1f}s）")

    policy = StreamPolicy(threshold=cfg.stream_char_threshold)
    results = []
    ok_all = True

    for label, text in TEXTS.items():
        print()
        print("-" * 100)
        print(f" {label}")
        print("-" * 100)

        routing = describe_routing(text, policy)
        print(f"  路由: {routing['mode']}（{routing['reason']}）")

        req = SynthesisRequest(text=text, voice_id="elysia")

        try:
            frames, result = stream_to_protocol_frames(
                engine, req,
                frame_bytes=DEFAULT_FRAME_BYTES,
                turn_id_short=0x1234,
                simulate_playback=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] 桥接失败: {type(exc).__name__}: {exc}")
            ok_all = False
            continue

        if not frames:
            print("  [FAIL] 未产出帧")
            ok_all = False
            continue

        print(f"  帧数 {result.frames}  总字节 {result.total_bytes}  "
              f"音频 {result.audio_s:.2f}s  首帧 {result.first_frame_ms:.0f}ms")
        print(f"  帧大小: 首 {result.frame_stats.first_frame_bytes}B / "
              f"最大 {result.frame_stats.max_frame_bytes}B / "
              f"最小 {result.frame_stats.min_frame_bytes}B")
        print(f"  背压: 领先 {result.backpressure['lead_sec']}s，"
              f"模拟等待 {result.backpressure['simulated_wait_s']}s")

        # ---- 校验 1：首帧满足首突发要求 ----
        min_burst = BYTES_PER_SEC * FIRST_BURST_MS // 1000
        if result.frame_stats.first_frame_bytes >= min_burst:
            print(f"  [PASS] 首帧 {result.frame_stats.first_frame_bytes}B "
                  f">= {min_burst}B（{FIRST_BURST_MS}ms）")
        else:
            # 短句总音频可能不足 300ms，此时首帧就是全部
            if result.total_bytes < min_burst:
                print(f"  [PASS] 音频总长 {result.total_bytes}B < {min_burst}B，"
                      "首帧即全部")
            else:
                print(f"  [FAIL] 首帧过小: {result.frame_stats.first_frame_bytes} "
                      f"< {min_burst}")
                ok_all = False

        # ---- 校验 2：每帧都能正确解码 ----
        decode_ok = True
        for i, f in enumerate(frames):
            try:
                d = decode_binary_frame(f)
                if d["seq"] != i:
                    print(f"  [FAIL] 帧 {i} seq={d['seq']} 不符")
                    decode_ok = False
                if d["turn_id_short"] != 0x1234:
                    print(f"  [FAIL] 帧 {i} turn_id 不符")
                    decode_ok = False
            except ValueError as exc:
                print(f"  [FAIL] 帧 {i} 解码失败: {exc}")
                decode_ok = False
        if decode_ok:
            print(f"  [PASS] {len(frames)} 帧全部解码正确（seq 连续、turn_id 一致）")
        else:
            ok_all = False

        # ---- 校验 3：模拟设备消费 ----
        dev = FakeDevice(ring_ms=200)
        for f in frames:
            dev.feed(f)
        dev.feed(encode_binary_frame(b"", seq=result.frames))
        dev.drain()

        s = dev.summary()
        print(f"  设备: 收 {s['frames']} 帧，"
              f"开播={s['started']}（第 {s['start_at_frame']} 帧），"
              f"收尾={s['got_end']}，播放 {s['played_s']:.2f}s")

        if s["seq_errors"]:
            print(f"  [FAIL] seq 错误: {s['seq_errors'][:3]}")
            ok_all = False
        else:
            print("  [PASS] 无 seq 跳变")

        if s["got_end"]:
            print("  [PASS] 收到结束标记")
        else:
            print("  [FAIL] 未收到结束标记")
            ok_all = False

        if s["started"]:
            print(f"  [PASS] 攒满 200ms 预缓冲后开播（第 {s['start_at_frame']} 帧）")
        else:
            print("  [WARN] 未开播（音频可能太短）")

        # ---- 校验 4：字节数守恒 ----
        if s["played_bytes"] + s["pending_bytes"] == result.total_bytes:
            print(f"  [PASS] 字节守恒: 播放+缓冲 = {result.total_bytes}")
        else:
            print(f"  [FAIL] 字节不守恒: "
                  f"{s['played_bytes'] + s['pending_bytes']} != {result.total_bytes}")
            ok_all = False

        results.append({
            "label": label,
            "frames": result.frames,
            "audio_s": result.audio_s,
            "first_frame_ms": result.first_frame_ms,
            "total_ms": result.total_ms,
            "start_at_frame": s["start_at_frame"],
            "routing": routing["mode"],
        })

    # ---- 汇总 ----
    print()
    print("=" * 100)
    print(" 汇总")
    print("=" * 100)
    print(f"  {'文本':<14}{'路由':>8}{'帧数':>6}{'音频':>9}"
          f"{'首帧ms':>9}{'桥接ms':>9}{'开播帧':>8}")
    print("  " + "-" * 68)
    for r in results:
        print(f"  {r['label']:<14}{r['routing']:>8}{r['frames']:>6}"
              f"{r['audio_s']:>8.2f}s{r['first_frame_ms']:>9.0f}"
              f"{r['total_ms']:>9.0f}{str(r['start_at_frame']):>8}")

    engine.shutdown()

    print()
    print("=" * 100)
    print(" 结论")
    print("=" * 100)
    if ok_all:
        print("  [PASS] 全部校验通过：真模型流式输出可被正确切成协议帧，")
        print("         模拟设备能正常开播、收尾、无 seq 跳变、字节守恒。")
        print()
        print("  这说明 zjy14 与 zjy13 的协议完全兼容，")
        print("  切换只需把 TTS_BASE_URL 从 :50000 改到 :50001。")
    else:
        print("  [FAIL] 存在校验失败项，见上方输出")

    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
