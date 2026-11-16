"""诊断：调度器是否真的限制了并发。

现象（verify_phase4.sh 第 5 项）：
  单请求 6.40s，3 个并发请求墙钟只有 8.03s。
  若真串行应该是 19.2s 左右。说明并发没被限制。

可能原因：
  A. Scheduler 没被创建（app.py 的 lifespan 没执行到）
  B. route 里 getattr(state, "scheduler") 拿到 None
  C. scheduler.run 被调用但信号量失效
  D. 请求根本没走 /synthesize（走了别的路径）

本脚本直接查服务状态，并对比调度器统计的前后变化。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "http://127.0.0.1:50006"


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as r:
        return json.loads(r.read())


def post_synth(text: str) -> tuple[float, int]:
    body = json.dumps({"text": text, "voice_id": "elysia"}).encode()
    req = urllib.request.Request(
        f"{BASE}/synthesize", data=body,
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            r.read()
        return time.perf_counter() - t0, r.status
    except Exception as e:  # noqa: BLE001
        return time.perf_counter() - t0, -1


def main() -> int:
    print("=" * 88)
    print(" 调度器并发限制诊断")
    print("=" * 88)

    st = get("/api/v1/system/status")
    print("\n[1] 调度器状态")
    sched = st.get("scheduler", {})
    print(f"  {json.dumps(sched, ensure_ascii=False)}")
    if not sched.get("enabled"):
        print("  [问题] 调度器未启用！")
        return 1
    print("  [OK] 调度器已启用")

    print("\n[2] 配置")
    c = st["config"]
    print(f"  max_concurrent_requests = {c['max_concurrent_requests']}")
    print(f"  max_queue_size = {c['max_queue_size']}")

    before = get("/api/v1/system/status")["scheduler"]
    print(f"\n[3] 测试前统计: served={before['total_served']} "
          f"active={before['active']} queued={before['queued']}")

    print("\n[4] 单请求基准")
    single, _ = post_synth("并发诊断文本，用于测量单请求耗时")
    print(f"  单请求 {single:.2f}s")

    print("\n[5] 3 并发请求")
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(lambda i: post_synth("并发诊断文本，用于测量单请求耗时"),
                              range(3)))
    wall = time.perf_counter() - t0
    times = [r[0] for r in results]
    print(f"  墙钟 {wall:.2f}s")
    print(f"  各请求耗时 {[round(t, 2) for t in times]}")

    after = get("/api/v1/system/status")["scheduler"]
    print(f"\n[6] 测试后统计: served={after['total_served']} "
          f"active={after['active']} queued={after['queued']}")
    print(f"  median_wait_ms = {after['median_wait_ms']}")
    print(f"  median_run_ms = {after['median_run_ms']}")

    print("\n[7] 判定")
    served_delta = after["total_served"] - before["total_served"]
    print(f"  served 增量: {served_delta}（期望 4 = 1 单测 + 3 并发）")

    if served_delta < 4:
        print("  [问题] 请求没有全部经过调度器")
    else:
        print("  [OK] 请求都经过调度器")

    if after["median_wait_ms"] > 100:
        print(f"  [OK] 存在排队（中位等待 {after['median_wait_ms']}ms）")
    else:
        print(f"  [问题] 几乎无排队（中位等待 {after['median_wait_ms']}ms），"
              "说明并发未被限制")

    # 串行预期：约 3 × single
    expected_serial = single * 3
    ratio = wall / single
    print(f"\n  墙钟/单请求 = {ratio:.2f}（串行应接近 3.0，并行接近 1.0）")
    if ratio > 2.0:
        print("  [OK] 串行执行")
    elif ratio > 1.5:
        print("  [部分] 介于串行与并行之间，可能有重叠")
    else:
        print("  [问题] 并行执行，调度器未生效")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
