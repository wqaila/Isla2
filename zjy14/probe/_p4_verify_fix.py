"""验证并发修复：调度器是否真正生效。

修复前症状：
  3 并发墙钟 = 单请求耗时（比值 1.00），median_wait = 0
  根因：async route 里直接调同步 sched.run()，卡死事件循环

修复后预期：
  3 并发墙钟 ≈ 3 × 单请求（比值接近 3.0）
  median_wait > 0（排队被记录）
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:50007"


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as r:
        return json.loads(r.read())


def post(text: str, rid: str) -> tuple[float, int]:
    body = json.dumps({"text": text, "voice_id": "elysia"}).encode()
    req = urllib.request.Request(
        f"{BASE}/synthesize", data=body,
        headers={"Content-Type": "application/json", "x-request-id": rid},
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            r.read()
        return time.perf_counter() - t0, r.status
    except Exception as e:  # noqa: BLE001
        print(f"    {rid} 失败: {type(e).__name__}: {e}")
        return time.perf_counter() - t0, -1


def main() -> int:
    print("=" * 88)
    print(" 并发修复验证")
    print("=" * 88)

    st = get("/api/v1/system/status")
    before = st["scheduler"]
    print(f"\n[1] 修复后配置: max_concurrent={before['max_concurrent']} "
          f"max_queue={before['max_queue']}")

    print("\n[2] 单请求基准（3 次取中位）")
    singles = []
    for i in range(3):
        t, code = post("并发修复验证的基准文本", f"fix-single-{i}")
        if code == 200:
            singles.append(t)
            print(f"    第{i+1}次 {t:.2f}s")
    if not singles:
        print("  [FAIL] 单请求全部失败")
        return 1
    singles.sort()
    single = singles[len(singles) // 2]
    print(f"  单请求中位 {single:.2f}s")

    print("\n[3] 3 并发请求")
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(lambda i: post("并发修复验证的基准文本", f"fix-c{i}"), range(3)))
    wall = time.perf_counter() - t0
    times = [r[0] for r in res]
    codes = [r[1] for r in res]
    print(f"    墙钟 {wall:.2f}s")
    print(f"    各请求 {[round(t, 2) for t in times]}")
    print(f"    状态 {codes}")

    after = get("/api/v1/system/status")["scheduler"]
    print("\n[4] 调度器统计")
    print(f"    served 增量 {after['total_served'] - before['total_served']}")
    print(f"    median_wait_ms {after['median_wait_ms']} (全部请求)")
    print(f"    排队请求数 {after.get('waited_count', 0)}")
    print(f"    排队中位 {after.get('median_wait_queued_ms', 0)}ms")
    print(f"    最大等待 {after.get('max_wait_ms', 0)}ms")
    print(f"    median_run_ms {after['median_run_ms']}")

    print("\n[5] 判定")
    ok = True
    ratio = wall / single if single > 0 else 0

    if not all(c == 200 for c in codes):
        print(f"    [FAIL] 有请求失败: {codes}")
        ok = False

    if ratio >= 2.2:
        print(f"    [PASS] 串行执行（墙钟/单请求 = {ratio:.2f}，接近 3.0）")
    elif ratio >= 1.5:
        print(f"    [部分] 比值 {ratio:.2f}，介于串行与并行之间")
    else:
        print(f"    [FAIL] 比值 {ratio:.2f}，仍是并行（事件循环可能仍被阻塞）")
        ok = False

    if after.get("waited_count", 0) > 0:
        print(f"    [PASS] 记录了排队（{after.get('waited_count', 0)} 个请求排过队，中位 {after.get('median_wait_queued_ms', 0)}ms）")
    else:
        print(f"    [FAIL] 无请求被记录为排队")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
