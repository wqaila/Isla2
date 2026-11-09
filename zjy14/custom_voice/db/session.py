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
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

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
        """事务上下文：异常时回滚。

        注意：``sqlite3`` 的 ``executescript()`` 会先隐式 COMMIT 掉已开启的事务，
        所以脚本类操作（建表）必须用 :meth:`executescript` 而不是在本上下文内执行。
        """
        conn = self.connect()
        with self._lock:
            started = not conn.in_transaction
            if started:
                conn.execute("BEGIN")
            try:
                yield conn
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            else:
                if conn.in_transaction:
                    conn.execute("COMMIT")

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
