"""推理调度：并发限制 + 排队 + 超时 + 取消。

为什么需要（对应原提示词"推理调度与服务"）：
  - 模型单例，多个请求共享，必须限制并发避免显存爆炸
  - 12GB 显存实测 TTS 峰值 3.45GB，理论上能跑 2-3 个并发，
    但 CUDA 上下文和中间张量会叠加，保守先限 1
  - 请求排队要有上限，否则无界队列会拖垮服务
  - 必须支持取消（用户打断、客户端断开）

设计：
  - 用 threading.Semaphore 控制并发（引擎是同步阻塞调用）
  - 用有界队列 + 超时拒绝，避免无限堆积
  - 每个请求带 request_id，支持取消标记
  - 统计排队时长、执行时长、拒绝次数
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from ..core.errors import ZJY14Error
from ..core.logging import get_logger

logger = get_logger("inference.scheduler")

T = TypeVar("T")


class QueueFullError(ZJY14Error):
    """队列已满，拒绝新请求。"""

    code = "queue_full"
    http_status = 429


class RequestTimeoutError(ZJY14Error):
    """排队超时。"""

    code = "queue_timeout"
    http_status = 503


class RequestCancelledError(ZJY14Error):
    """请求被取消。"""

    code = "request_cancelled"
    http_status = 499


@dataclass(slots=True)
class SchedulerStats:
    """调度器统计。"""

    max_concurrent: int
    max_queue: int
    active: int = 0
    queued: int = 0
    total_served: int = 0
    total_rejected: int = 0
    total_timeout: int = 0
    total_cancelled: int = 0
    #: 最近 100 次排队等待时长（毫秒）
    recent_waits_ms: list[float] = field(default_factory=list)
    #: 最近 100 次执行时长（毫秒）
    recent_runs_ms: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def med(v: list[float]) -> float:
            if not v:
                return 0.0
            s = sorted(v)
            return round(s[len(s) // 2], 1)

        # 只统计「真正排队过」的请求（等待 > 10ms）。
        # 否则大量零等待请求会把中位数拉成 0，指标失去意义。
        waited = [w for w in self.recent_waits_ms if w > 10.0]

        return {
            "max_concurrent": self.max_concurrent,
            "max_queue": self.max_queue,
            "active": self.active,
            "queued": self.queued,
            "total_served": self.total_served,
            "total_rejected": self.total_rejected,
            "total_timeout": self.total_timeout,
            "total_cancelled": self.total_cancelled,
            # 全部请求的等待中位数（含零等待）
            "median_wait_ms": med(self.recent_waits_ms),
            # 仅统计真正排过队的请求，更能反映拥塞程度
            "waited_count": len(waited),
            "median_wait_queued_ms": med(waited),
            "max_wait_ms": round(max(self.recent_waits_ms), 1)
            if self.recent_waits_ms
            else 0.0,
            "median_run_ms": med(self.recent_runs_ms),
        }


class Scheduler:
    """并发控制器。

    用法：
        sched = Scheduler(max_concurrent=1, max_queue=8)
        result = sched.run("req-123", lambda: engine.synthesize(req))
    """

    def __init__(
        self,
        max_concurrent: int = 1,
        max_queue: int = 8,
        queue_timeout_s: float = 60.0,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent 必须 >= 1")
        if max_queue < 0:
            raise ValueError("max_queue 不能为负")

        self._sem = threading.BoundedSemaphore(max_concurrent)
        self._lock = threading.Lock()
        self._stats = SchedulerStats(
            max_concurrent=max_concurrent, max_queue=max_queue
        )
        self._max_queue = max_queue
        self._queue_timeout_s = queue_timeout_s
        self._cancelled: set[str] = set()

    # ------------------------------------------------------------------

    def cancel(self, request_id: str) -> bool:
        """标记请求为已取消。正在执行的请求会在下一个检查点退出。"""
        with self._lock:
            self._cancelled.add(request_id)
            # 避免无限增长
            if len(self._cancelled) > 1000:
                self._cancelled = set(list(self._cancelled)[-500:])
        logger.info(
            "请求已标记取消",
            extra={"extra_fields": {"request_id": request_id}},
        )
        return True

    def is_cancelled(self, request_id: str) -> bool:
        with self._lock:
            return request_id in self._cancelled

    def _clear_cancelled(self, request_id: str) -> None:
        with self._lock:
            self._cancelled.discard(request_id)

    # ------------------------------------------------------------------

    def run(
        self,
        request_id: str,
        fn: Callable[[], T],
        *,
        timeout_s: float | None = None,
    ) -> T:
        """在并发控制下执行 fn。

        :raises QueueFullError: 队列已满
        :raises RequestTimeoutError: 排队超时
        :raises RequestCancelledError: 请求被取消
        """
        # 队列容量检查
        with self._lock:
            if self._stats.queued >= self._max_queue:
                self._stats.total_rejected += 1
                raise QueueFullError(
                    f"服务繁忙：队列已满（{self._max_queue} 个等待中），请稍后重试",
                    detail={
                        "queued": self._stats.queued,
                        "max_queue": self._max_queue,
                        "active": self._stats.active,
                    },
                )
            self._stats.queued += 1

        wait_start = time.perf_counter()
        acquired = self._sem.acquire(timeout=timeout_s or self._queue_timeout_s)
        wait_ms = (time.perf_counter() - wait_start) * 1000

        with self._lock:
            self._stats.queued -= 1
            if not acquired:
                self._stats.total_timeout += 1
            else:
                self._stats.active += 1
                self._stats.recent_waits_ms.append(wait_ms)
                if len(self._stats.recent_waits_ms) > 100:
                    self._stats.recent_waits_ms.pop(0)

        if not acquired:
            raise RequestTimeoutError(
                f"排队超时（{timeout_s or self._queue_timeout_s}s），"
                "服务可能过载，请稍后重试",
                detail={"waited_ms": round(wait_ms)},
            )

        # 拿到槽位后再检查一次取消
        if self.is_cancelled(request_id):
            with self._lock:
                self._stats.active -= 1
                self._stats.total_cancelled += 1
            self._sem.release()
            self._clear_cancelled(request_id)
            raise RequestCancelledError("请求在排队期间被取消")

        run_start = time.perf_counter()
        try:
            result = fn()
        finally:
            run_ms = (time.perf_counter() - run_start) * 1000
            with self._lock:
                self._stats.active -= 1
                self._stats.total_served += 1
                self._stats.recent_runs_ms.append(run_ms)
                if len(self._stats.recent_runs_ms) > 100:
                    self._stats.recent_runs_ms.pop(0)
            self._sem.release()
            self._clear_cancelled(request_id)

        logger.info(
            "请求执行完成",
            extra={
                "extra_fields": {
                    "request_id": request_id,
                    "wait_ms": round(wait_ms, 1),
                    "run_ms": round(run_ms, 1),
                }
            },
        )
        return result

    # ------------------------------------------------------------------
    # 「占用槽位直到流结束」的变体（供流式响应使用）
    #
    # 为什么需要单独一组方法（2026-11-25）：
    #   流式响应要「整个传输期间都占一个并发名额」，而 run() 是
    #   「fn 返回后释放」。原实现由路由直接操作 self._sem：
    #       acquired = sched._sem.acquire(timeout=queue_timeout_s)
    #   这有三个问题：
    #     1) **在 async 函数里同步阻塞**：threading.Semaphore.acquire
    #        是阻塞调用，最长会把整个事件循环冻住 queue_timeout_s
    #        （默认 60s），期间所有其他请求（含 /health、监控页、
    #        正在传输的其他流）全部无响应。
    #     2) **绕过队列上限检查**：run() 会先检查 queued >= max_queue
    #        并抛 QueueFullError(429)，直连 _sem 完全跳过，
    #        队列可以被无限堆积，显存与延迟都会失控。
    #     3) **统计失真**：active / queued / total_rejected 都不更新，
    #        /system/status 里这些数字与实际严重不符。
    #
    #   本组方法把 run() 的那套检查抽出来，供调用方「先 acquire、
    #   传输结束再 release」。**acquire_slot 仍是阻塞调用**，
    #   async 环境必须用 ``run_in_threadpool`` 包一层。
    # ------------------------------------------------------------------

    def acquire_slot(
        self, request_id: str, *, timeout_s: float | None = None
    ) -> None:
        """获取一个并发槽位，由调用方在结束时调用 :meth:`release_slot`。

        与 :meth:`run` 的区别：run 在 fn 返回后自动释放；
        本方法把释放时机交给调用方，适配「流式响应跨多个 await」的场景。

        **这是阻塞调用**。在 async 上下文里必须这样用：

            await run_in_threadpool(sched.acquire_slot, req_id)

        :raises QueueFullError: 队列已满
        :raises RequestTimeoutError: 排队超时
        :raises RequestCancelledError: 请求在排队期间被取消
        """
        limit = timeout_s if timeout_s is not None else self._queue_timeout_s

        # 队列容量检查（与 run() 完全一致，不能因为是流式就跳过）
        with self._lock:
            if self._stats.queued >= self._max_queue:
                self._stats.total_rejected += 1
                raise QueueFullError(
                    f"服务繁忙：队列已满（{self._max_queue} 个等待中），请稍后重试",
                    detail={
                        "queued": self._stats.queued,
                        "max_queue": self._max_queue,
                        "active": self._stats.active,
                    },
                )
            self._stats.queued += 1

        wait_start = time.perf_counter()
        acquired = self._sem.acquire(timeout=limit)
        wait_ms = (time.perf_counter() - wait_start) * 1000

        with self._lock:
            self._stats.queued -= 1
            if not acquired:
                self._stats.total_timeout += 1
            else:
                self._stats.active += 1
                self._stats.recent_waits_ms.append(wait_ms)
                if len(self._stats.recent_waits_ms) > 100:
                    self._stats.recent_waits_ms.pop(0)

        if not acquired:
            raise RequestTimeoutError(
                f"排队超时（{limit}s），服务可能过载，请稍后重试",
                detail={"waited_ms": round(wait_ms)},
            )

        # 拿到槽位后再检查一次取消（与 run() 一致）
        if self.is_cancelled(request_id):
            with self._lock:
                self._stats.active -= 1
                self._stats.total_cancelled += 1
            self._sem.release()
            self._clear_cancelled(request_id)
            raise RequestCancelledError("请求在排队期间被取消")

    def release_slot(self, request_id: str, *, run_ms: float = 0.0) -> None:
        """归还由 :meth:`acquire_slot` 取得的槽位。

        必须与 acquire_slot 严格成对（用 try/finally 保证），
        否则槽位泄漏会让后续请求永久等待。
        """
        with self._lock:
            # 用 max(0, ...) 兜底：重复 release 不该把 active 变成负数
            # （重复 release 本身会触发 BoundedSemaphore 的 ValueError，
            # 这里只保护统计字段不被污染）
            self._stats.active = max(0, self._stats.active - 1)
            self._stats.total_served += 1
            if run_ms > 0:
                self._stats.recent_runs_ms.append(run_ms)
                if len(self._stats.recent_runs_ms) > 100:
                    self._stats.recent_runs_ms.pop(0)
        self._sem.release()
        self._clear_cancelled(request_id)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return self._stats.to_dict()


__all__ = [
    "QueueFullError",
    "RequestCancelledError",
    "RequestTimeoutError",
    "Scheduler",
    "SchedulerStats",
]
