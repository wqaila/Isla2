"""定位：为什么服务端调度器的 median_wait_ms 为 0。

单元测试证明 Scheduler 本身正确（等待 300.7ms 被记录）。
服务端却显示 0。差异点：

1. 服务端每个请求的 request_id 可能相同或为空
   -> route 里用 get_request_id()，若中间件没注入则为空
   -> 空 id 会命中 _cancelled 集合的清理逻辑？

2. 服务端可能创建了多个 Scheduler 实例
   -> 每个实例统计独立

3. 服务端的并发请求实际没有排队
   -> 因为 ThreadPoolExecutor 的 3 个请求到达时间不同

本脚本直接检查服务端日志与统计细节。
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


def post(text: str, rid: str | None = None) -> tuple[float, int, str]:
    body = json.dumps({"text": text, "voice_id": "elysia"}).encode()
    headers = {"Content-Type": "application/json"}
    if rid:
        headers["x-request-id"] = rid
    req = urllib.request.Request(f"{BASE}/synthesize", data=body, headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            r.read()
        return time.perf_counter() - t0, r.status, r.headers.get("x-request-id", "")
    except Exception as e:  # noqa: BLE001
        return time.perf_counter() - t0, -1, str(e)


def main() -> int:
    print("=" * 88)
    print(" 服务端调度器统计异常定位")
    print("=" * 88)

    st = get("/api/v1/system/status")
    before = st["scheduler"]
    print(f"\n[1] 测试前: served={before['total_served']} "
          f"median_wait={before['median_wait_ms']}ms "
          f"median_run={before['median_run_ms']}ms")

    print("\n[2] 单个请求（显式 request_id）")
    t, code, rid = post("单请求测试文本", rid="diag-single-001")
    print(f"  耗时 {t:.2f}s  状态 {code}  request_id={rid}")

    after1 = get("/api/v1/system/status")["scheduler"]
    print(f"  served={after1['total_served']} "
          f"median_wait={after1['median_wait_ms']}ms "
          f"median_run={after1['median_run_ms']}ms")

    print("\n[3] 3 并发请求（显式不同 request_id）")
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(lambda i: post("并发测试文本", rid=f"diag-c{i}"), range(3)))
    wall = time.perf_counter() - t0
    print(f"  墙钟 {wall:.2f}s")
    for i, (t, code, rid) in enumerate(res):
        print(f"    请求{i}: {t:.2f}s  状态 {code}  rid={rid}")

    after2 = get("/api/v1/system/status")["scheduler"]
    print(f"\n[4] 测试后: served={after2['total_served']} "
          f"median_wait={after2['median_wait_ms']}ms "
          f"median_run={after2['median_run_ms']}ms")
    print(f"  served 增量: {after2['total_served'] - before['total_served']}")

    print("\n[5] 分析")
    if after2["total_served"] - before["total_served"] != 4:
        print(f"  [问题] served 增量应为 4，实际 {after2['total_served'] - before['total_served']}")
        print("         说明部分请求没经过调度器")
    else:
        print("  [OK] 4 个请求都经过调度器")

    if after2["median_wait_ms"] > 0:
        print(f"  [OK] 记录了排队（{after2['median_wait_ms']}ms）")
    else:
        print("  [问题] median_wait 仍为 0")
        print("         可能原因：FastAPI 的 async route 里调用同步 sched.run()，")
        print("         每个请求在各自线程池线程中执行，但事件循环是单线程的 ——")
        print("         若 sched.run 阻塞了事件循环，请求会串行到达，不会排队。")

    # 关键判据：墙钟 vs 单请求
    single = res[0][0] if res else 0
    print(f"\n  单请求 {t:.2f}s / 3并发墙钟 {wall:.2f}s / 比值 {wall/max(t,0.01):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
