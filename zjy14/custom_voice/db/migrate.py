"""建表与版本迁移。

要求：幂等。重复调用不报错、不重复建表。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.logging import get_logger
from .models import SCHEMA_SQL, SCHEMA_VERSION
from .session import Database

logger = get_logger("db.migrate")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def current_version(db: Database) -> int:
    """读取已应用的 schema 版本。空库返回 0。"""
    conn = db.connect()
    # 先确保 version 表存在，否则查询会失败
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def migrate(db: Database) -> int:
    """执行迁移，返回最终版本号。幂等。

    说明：``SCHEMA_SQL`` 里全部是 ``CREATE ... IF NOT EXISTS``，
    因此从任意旧版本升级都是「重跑一遍建表脚本 + 补记版本号」，
    不需要为每个版本写单独的 ALTER 步骤。

    这对新增索引/新增表是安全的；若将来需要给已有表加列
    （SQLite 的 ``ALTER TABLE ADD COLUMN``），必须在此处按版本分支处理。
    """
    conn = db.connect()
    applied = current_version(db)

    if applied >= SCHEMA_VERSION:
        logger.info(
            "schema 已是最新，跳过迁移",
            extra={"extra_fields": {"current": applied, "target": SCHEMA_VERSION}},
        )
        return applied

    # 建表脚本必须用 executescript（会隐式提交），不能放在 transaction() 内
    db.executescript(SCHEMA_SQL)

    with db.transaction() as tx:
        tx.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, _now()),
        )

    logger.info(
        "schema 迁移完成",
        extra={"extra_fields": {"from": applied, "to": SCHEMA_VERSION}},
    )
    return SCHEMA_VERSION


__all__ = ["current_version", "migrate"]
