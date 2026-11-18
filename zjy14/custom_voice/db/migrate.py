"""建表与版本迁移。

要求：幂等。重复调用不报错、不重复建表。
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.logging import get_logger
from .models import ALTER_STATEMENTS, SCHEMA_SQL, SCHEMA_VERSION
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


def _apply_alters(db: Database, from_version: int) -> None:
    """执行需要 ALTER 的迁移步骤。

    ``SCHEMA_SQL`` 的 ``CREATE TABLE IF NOT EXISTS`` 对已存在的表不会补新列，
    所以「给旧表加列」必须单独处理。

    容错：SQLite 的 ``ALTER TABLE ADD COLUMN`` 重复执行会报
    "duplicate column name"。若列已存在（例如上次迁移中断后重跑），
    跳过即可，不应视为失败。
    """
    conn = db.connect()
    for target, stmt in ALTER_STATEMENTS:
        if from_version >= target:
            continue
        try:
            conn.execute(stmt)
            logger.info(
                "schema 加列完成",
                extra={"extra_fields": {"version": target, "sql": stmt}},
            )
        except Exception as exc:  # noqa: BLE001
            if "duplicate column" in str(exc).lower():
                logger.debug(
                    "列已存在，跳过",
                    extra={"extra_fields": {"version": target}},
                )
                continue
            raise


def migrate(db: Database) -> int:
    """执行迁移，返回最终版本号。幂等。

    流程：
      1. 若已是最新，直接返回
      2. 跑 ``SCHEMA_SQL``（全 IF NOT EXISTS，安全重复执行）
      3. 跑 ``ALTER_STATEMENTS``（给已有表补列，按版本跳过 + 容错）
      4. 记录版本号
    """
    applied = current_version(db)

    if applied >= SCHEMA_VERSION:
        logger.info(
            "schema 已是最新，跳过迁移",
            extra={"extra_fields": {"current": applied, "target": SCHEMA_VERSION}},
        )
        return applied

    # 建表脚本必须用 executescript（会隐式提交），不能放在 transaction() 内
    db.executescript(SCHEMA_SQL)

    # 给已有表补列（必须在建表之后，因为可能引用新表）
    _apply_alters(db, applied)

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
