"""SQLite 连接管理。

要点：
- WAL 模式（并发读性能好，适合"写入合成记录 + 读取仪表盘"并存）
- 外键约束必须每个连接单独开启（SQLite 默认关闭）
- 连接不跨线程共享（check_same_thread=False 配合锁使用）
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..core.paths import db_path as default_db_path


class Database:
    """轻量连接管理器。

    Phase 1 用单连接 + 锁（并发上限为 1，够用）。
    Phase 4 引入并发后再换成连接池。

    并发说明（2026-11-25 加固）：
      服务已启用流式并发（`/synthesize_stream` 在流传输期间独占槽位），
      而合成记录落库发生在**每个流的收尾**，所以写操作确实可能并发。
      现存两个隐患，都在 :meth:`transaction` 里修掉了：
        1. **嵌套假提交**：内层事务结束时会 COMMIT，把外层还没做完的
           工作提前提交，外层出错回滚不了。现在用 `_tx_depth` 计数，
           只有最外层才真正 BEGIN/COMMIT/ROLLBACK。
        2. **非重入锁**：嵌套时同一线程会卡在自身的 `threading.Lock` 上。
           现改用 `threading.RLock`。
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        # RLock：transaction() 可能嵌套调用（见类文档），
        # 普通 Lock 会让同一线程在第二次进入时自锁。
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        #: 当前线程的事务嵌套深度（只有最外层才真正 BEGIN/COMMIT）
        self._tx_depth = 0

    # ------------------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        """建立连接并配置 PRAGMA。"""
        if self._conn is not None:
            return self._conn

        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,  # 自己管理事务
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """事务上下文：异常时回滚，且**支持嵌套**。

        嵌套语义（2026-11-25 加固）：
          - 只有**最外层**执行 ``BEGIN`` / ``COMMIT`` / ``ROLLBACK``
          - 内层只记账，既不提交也不回滚 —— 否则内层结束时的 COMMIT
            会把外层尚未完成的工作一起提交，外层随后出错就回滚不掉
          - 内层抛异常会一路冒泡到最外层，由最外层统一 ROLLBACK，
            因此内层异常同样能撤销整个事务

        注意：``sqlite3`` 的 ``executescript()`` 会先隐式 COMMIT 掉已开启的事务，
        所以脚本类操作（建表）必须用 :meth:`executescript` 而不是在本上下文内执行。
        """
        conn = self.connect()
        with self._lock:
            depth = self._tx_depth
            started = False
            if depth == 0 and not conn.in_transaction:
                conn.execute("BEGIN")
                started = True
            self._tx_depth += 1
            try:
                yield conn
            except Exception:
                # 只有最外层回滚；内层把异常继续抛给最外层处理
                if depth == 0 and conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            else:
                if depth == 0 and (started or conn.in_transaction):
                    conn.execute("COMMIT")
            finally:
                self._tx_depth -= 1

    def executescript(self, script: str) -> None:
        """执行多语句脚本（建表等）。

        单独提供此方法是因为 ``executescript`` 会隐式提交当前事务，
        不能放在 ``transaction()`` 内。
        """
        conn = self.connect()
        with self._lock:
            conn.executescript(script)

    # ------------------------------------------------------------------

    def table_names(self) -> list[str]:
        conn = self.connect()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    def foreign_keys_enabled(self) -> bool:
        conn = self.connect()
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        return bool(row[0]) if row else False

    def journal_mode(self) -> str:
        conn = self.connect()
        row = conn.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]) if row else ""


__all__ = ["Database"]
