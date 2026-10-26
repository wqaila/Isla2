"""ASR 命令行工具：识别单个或多个 WAV 文件，输出文本与计时。

用法：
    # 单文件
    python -m asr.transcribe test.wav

    # 目录批量
    python -m asr.transcribe tests/audio --pattern *.wav

    # 只检查环境，不加载模型
    python -m asr.transcribe --check-env

    # 输出 JSON
    python -m asr.transcribe test.wav --json

退出码：0 成功；2 环境/配置错误；3 识别失败。
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
from pathlib import Path

# Windows 控制台中文
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

from .engine import ASREngine, ASRError, CUDANotAvailableError, check_device

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _print_device(info) -> None:
    print("=" * 60)
    print("运行环境")
    print("=" * 60)
    print(f"  torch          : {info.torch_version}")
    print(f"  CUDA 版本      : {info.cuda_version}")
    print(f"  CUDA 可用      : {info.cuda_available}")
    print(f"  GPU            : {info.device_name}")
    if info.capability:
        print(f"  计算能力       : sm_{info.capability[0]}{info.capability[1]}")
    print(f"  编译架构列表   : {', '.join(info.arch_list) if info.arch_list else '(空)'}")
    print(f"  架构匹配       : {info.arch_match}")
    print(f"  FunASR 版本    : {info.funasr_version}")
    print(f"  实际使用       : {'GPU' if info.using_gpu else 'CPU'}")
    if info.cuda_available and info.arch_match is False:
        print()
        print("  !! 警告：PyTorch 未编译 sm_120 支持，实际会回退到 PTX JIT（极慢）")
        print("  !! 请安装 torch>=2.7.0 的 cu128 构建")
    print()


def collect_files(target: Path, pattern: str) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        files = sorted(target.glob(pattern))
        if not files:
            # 递归再试一次
            files = sorted(target.rglob(pattern))
        return [f for f in files if f.is_file()]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description="FunASR 本地识别（Paraformer 中文）")
    ap.add_argument("input", nargs="?", help="WAV 文件或目录")
    ap.add_argument("--pattern", default="*.wav", help="目录模式下的文件匹配（默认 *.wav）")
    ap.add_argument("--check-env", action="store_true", help="只检查环境，不加载模型")
    ap.add_argument("--cpu", action="store_true", help="允许 CPU 回退")
    ap.add_argument("--no-vad", action="store_true", help="不启用 VAD")
    ap.add_argument("--punc", action="store_true", help="启用标点恢复")
    ap.add_argument("--cache-dir", default=None, help="模型缓存目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    _setup_logging(args.verbose)

    # ---- 环境检查 ----
    try:
        info = check_device(require_gpu=not args.cpu)
    except CUDANotAvailableError as exc:
        print(f"[环境错误] {exc}", file=sys.stderr)
        return 2
    except ASRError as exc:
        print(f"[环境错误] {exc}", file=sys.stderr)
        return 2

    if args.check_env:
        _print_device(info)
        return 0

    if not args.input:
        print("[错误] 需要提供 WAV 文件或目录（或用 --check-env）", file=sys.stderr)
        return 2

    target = Path(args.input)
    files = collect_files(target, args.pattern)
    if not files:
        print(f"[错误] 没有找到音频文件: {target} (pattern={args.pattern})", file=sys.stderr)
        return 2

    # ---- 加载模型 ----
    engine = ASREngine(
        cache_dir=args.cache_dir,
        require_gpu=not args.cpu,
        use_vad=not args.no_vad,
        use_punc=args.punc,
    )
    try:
        dev = engine.load()
    except ASRError as exc:
        print(f"[加载失败] {exc}", file=sys.stderr)
        return 2

    if not args.json:
        _print_device(dev)
        print(f"模型加载耗时: {engine.load_ms:.0f} ms")
        if engine.warmup_ms is not None:
            print(f"预热耗时    : {engine.warmup_ms:.0f} ms")
        print()

    # ---- 逐个识别 ----
    results = []
    failures = 0
    for i, f in enumerate(files, 1):
        if not args.json:
            print(f"[{i}/{len(files)}] {f.name}")
        try:
            r = engine.transcribe_file(f)
        except ASRError as exc:
            failures += 1
            if not args.json:
                print(f"  失败: {exc}")
            results.append({"file": str(f), "error": str(exc)})
            continue

        results.append({"file": str(f), **r.to_dict()})
        if not args.json:
            print(f"  时长 {r.audio_duration_s:.2f}s | 推理 {r.infer_ms:.0f}ms | "
                  f"RTF {r.realtime_factor:.3f} | 总 {r.total_ms:.0f}ms")
            print(f"  识别: {r.text or '(空)'}")
            print()

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if failures == 0 else 3

    # ---- 汇总 ----
    ok = [r for r in results if "error" not in r]
    if len(files) > 1 and ok:
        rtfs = [r["realtime_factor"] for r in ok]
        infers = [r["infer_ms"] for r in ok]
        durs = [r["audio_duration_s"] for r in ok]
        print("=" * 60)
        print("汇总")
        print("=" * 60)
        print(f"  成功 {len(ok)} / {len(files)}，失败 {failures}")
        print(f"  音频总时长 : {sum(durs):.2f} s")
        print(f"  推理均值   : {statistics.fmean(infers):.0f} ms")
        print(f"  RTF 均值   : {statistics.fmean(rtfs):.3f}")
        if len(rtfs) >= 2:
            s = sorted(rtfs)
            p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
            print(f"  RTF P95    : {p95:.3f}")

    return 0 if failures == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
