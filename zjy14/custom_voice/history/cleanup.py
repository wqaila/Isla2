"""自动清理：按保留期删除过期的合成记录与日志。

为什么需要：
  合成记录与日志会持续增长。长期运行时表会变大，
  查询变慢、磁盘占用上升。手工 DELETE 容易忘。

设计要点：
  - **后台线程 + 固定间隔**，不依赖外部 cron
  - **保留期可配置**，0 表示不清理
  - **每次清理写审计**（cleanup_logs 表），便于事后追溯
  - **失败不影响服务**：清理出错只记日志，不抛到主流程
  - **日志文件按天轮转已由 TimedRotatingFileHandler 处理**，
    这里只清理「超过保留期的归档日志文件」

启动时立即执行一次（清理上次运行期间累积的过期数据），
之后按间隔执行。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.logging import get_logger
from ..core.paths import log_dir

logger = get_logger("history.cleanup")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def cutoff_iso(days: int) -> str:
    """返回「days 天前」的 ISO8601 时间串（UTC）。

    :param days: 保留天数。0 表示「现在」（即清空全部）。
    """
    return (_utc_now() - timedelta(days=max(0, days))).isoformat(timespec="seconds")


def purge_archived_logs(retain_days: int) -> list[str]:
    """删除超过保留期的归档日志文件。

    只删 ``zjy14.log.YYYY-MM-DD`` 形式的归档，**不删当前日志**
    （``zjy14.log``）。

    :return: 被删除的文件名列表
    """
    if retain_days <= 0:
        return []

    d = log_dir()
    if not d.is_dir():
        return []

    cutoff = _utc_now() - timedelta(days=retain_days)
    removed: list[str] = []

    for p in d.glob("zjy14.log.*"):
        if not p.is_file():
            continue
        # 用文件修改时间判断，比解析文件名更稳（轮转文件名格式可能变）
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        if mtime >= cutoff:
            continue
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            logger.exception(
                "删除归档日志失败",
                extra={"extra_fields": {"file": p.name}},
            )

    return removed


class CleanupScheduler:
    """定期清理的后台调度器。

    用法（在 app lifespan 里）：
        sched = CleanupScheduler(db, retain_days=30, interval_h=24)
        sched.start()
        ...
        sched.stop()
    """

    def __init__(
        self,
        db: Any,
        *,
        retain_days: int = 30,
        log_retain_days: int = 14,
        interval_hours: float = 24.0,
        enabled: bool = True,
    ) -> None:
        self._db = db
        self._retain_days = retain_days
        self._log_retain_days = log_retain_days
        self._interval_s = max(60.0, interval_hours * 3600)
        self._enabled = enabled

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_run: dict[str, Any] | None = None
        self._runs = 0

    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动后台线程。禁用时为空操作。"""
        if not self._enabled:
            logger.info("自动清理未启用（retain_days=0）")
            return
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="zjy14-cleanup", daemon=True
        )
        self._thread.start()
        logger.info(
            "自动清理已启动",
            extra={
                "extra_fields": {
                    "retain_days": self._retain_days,
                    "log_retain_days": self._log_retain_days,
                    "interval_h": round(self._interval_s / 3600, 2),
                }
            },
        )

    def stop(self, timeout: float = 5.0) -> None:
        """停止后台线程。"""
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        self._thread = None

    # ------------------------------------------------------------------

    def _loop(self) -> None:
        """主循环：立即跑一次，之后按间隔跑。"""
        # 启动后稍等，避免与模型加载抢 IO
        if self._stop.wait(5.0):
            return

        while not self._stop.is_set():
            try:
                self.run_once(triggered_by="scheduled")
            except Exception:  # noqa: BLE001
                logger.exception("自动清理失败（不影响服务）")

            # 用 wait 而非 sleep，便于及时响应停止
            if self._stop.wait(self._interval_s):
                return

    def run_once(self, *, triggered_by: str = "manual") -> dict[str, Any]:
        """执行一次清理，返回结果摘要。"""
        from .repository import SynthesisHistoryRepository

        started = time.perf_counter()
        repo = SynthesisHistoryRepository(self._db)

        cutoff = cutoff_iso(self._retain_days)
        removed_rows = 0
        if self._retain_days > 0:
            removed_rows = repo.delete_before(cutoff, triggered_by=triggered_by)

        removed_files = purge_archived_logs(self._log_retain_days)

        elapsed_ms = (time.perf_counter() - started) * 1000
        summary = {
            "triggered_by": triggered_by,
            "retain_days": self._retain_days,
            "log_retain_days": self._log_retain_days,
            "cutoff": cutoff,
            "removed_records": removed_rows,
            "removed_log_files": removed_files,
            "elapsed_ms": round(elapsed_ms, 1),
            "at": _utc_now().isoformat(timespec="seconds"),
        }

        self._last_run = summary
        self._runs += 1

        if removed_rows or removed_files:
            logger.info("清理完成", extra={"extra_fields": summary})
        else:
            logger.debug("清理完成（无可清理项）", extra={"extra_fields": summary})

        return summary

    def stats(self) -> dict[str, Any]:
        """供 /system/status 展示。"""
        return {
            "enabled": self._enabled,
            "retain_days": self._retain_days,
            "log_retain_days": self._log_retain_days,
            "interval_h": round(self._interval_s / 3600, 2),
            "runs": self._runs,
            "last_run": self._last_run,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
        }


__all__ = [
    "CleanupScheduler",
    "cutoff_iso",
    "purge_archived_logs",
]
