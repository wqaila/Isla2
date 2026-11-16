"""最小复现：验证 Scheduler 的等待时长统计是否正确。"""

from __future__ import annotations

import sys
import threading
import time

sys.path.insert(0, "/mnt/d/xiangmudaima/zjy14")

from custom_voice.inference.scheduler import Scheduler


def main() -> int:
    print("=" * 70)
    print(" Scheduler 等待时长统计验证")
    print("=" * 70)

    s = Scheduler(max_concurrent=1, max_queue=8, queue_timeout_s=30)

    release = threading.Event()
    started = threading.Event()

    def slow() -> str:
        started.set()
        release.wait(timeout=10)
        return "ok"

    # 第一个请求占住槽位
    t1 = threading.Thread(target=lambda: s.run("r1", slow))
    t1.start()
    started.wait(timeout=5)

    # 再起两个，它们必须排队
    results: list[float] = []

    def queued(rid: str) -> None:
        t0 = time.perf_counter()
        try:
            s.run(rid, lambda: "ok")
            results.append(time.perf_counter() - t0)
        except Exception as e:  # noqa: BLE001
            results.append(-1)
            print(f"  {rid} 失败: {e}")

    t2 = threading.Thread(target=lambda: queued("r2"))
    t3 = threading.Thread(target=lambda: queued("r3"))
    t2.start()
    t3.start()

    time.sleep(0.3)
    print(f"\n  排队中时的统计: {s.stats()}")

    release.set()
    t1.join(timeout=10)
    t2.join(timeout=10)
    t3.join(timeout=10)

    print(f"\n  各排队请求的总耗时: {[round(r, 2) for r in results]}")

    st = s.stats()
    print("\n  最终统计:")
    for k, v in st.items():
        print(f"    {k}: {v}")

    print("\n  判定:")
    ok = True
    if st["total_served"] != 3:
        print(f"    [FAIL] total_served 应为 3，实际 {st['total_served']}")
        ok = False
    if st["median_wait_ms"] <= 0:
        print(f"    [FAIL] median_wait_ms 应为正数（有排队），实际 {st['median_wait_ms']}")
        ok = False
    else:
        print(f"    [OK] median_wait_ms = {st['median_wait_ms']}ms（正确记录了排队）")
    if st["active"] != 0:
        print(f"    [FAIL] active 应归零，实际 {st['active']}")
        ok = False
    if st["queued"] != 0:
        print(f"    [FAIL] queued 应归零，实际 {st['queued']}")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
