"""SQLite 数据层测试：建表幂等、PRAGMA 正确。"""

from __future__ import annotations

from pathlib import Path

from custom_voice.db.migrate import current_version, migrate
from custom_voice.db.models import SCHEMA_VERSION
from custom_voice.db.session import Database

EXPECTED_TABLES = {
    "characters",
    "speakers",
    "reference_audio",
    "synthesis_logs",
    "metrics",
    "schema_version",
}


def test_migrate_creates_tables(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    try:
        migrate(db)
        assert EXPECTED_TABLES <= set(db.table_names())
    finally:
        db.close()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    try:
        v1 = migrate(db)
        v2 = migrate(db)
        assert v1 == v2 == SCHEMA_VERSION
        assert current_version(db) == SCHEMA_VERSION
    finally:
        db.close()


def test_foreign_keys_enabled(tmp_path: Path) -> None:
    """SQLite 默认关闭外键，必须每个连接显式开启。"""
    db = Database(tmp_path / "test.db")
    try:
        db.connect()
        assert db.foreign_keys_enabled() is True
    finally:
        db.close()


def test_wal_mode(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    try:
        db.connect()
        assert db.journal_mode().lower() == "wal"
    finally:
        db.close()


def test_transaction_rollback(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    try:
        migrate(db)
        try:
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO metrics (name, value, labels, created_at) "
                    "VALUES ('x', 1.0, '{}', '2026-01-01T00:00:00')"
                )
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        conn = db.connect()
        row = conn.execute("SELECT COUNT(*) AS c FROM metrics").fetchone()
        assert row["c"] == 0
    finally:
        db.close()


def test_transaction_commit(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.db")
    try:
        migrate(db)
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO metrics (name, value, labels, created_at) "
                "VALUES ('y', 2.0, '{}', '2026-01-01T00:00:00')"
            )
        conn = db.connect()
        row = conn.execute("SELECT COUNT(*) AS c FROM metrics").fetchone()
        assert row["c"] == 1
    finally:
        db.close()


def test_synthesis_log_accepts_row(tmp_path: Path) -> None:
    """确认 synthesis_logs 表结构与写入语句匹配。"""
    db = Database(tmp_path / "test.db")
    try:
        migrate(db)
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO synthesis_logs ("
                "request_id, text, emotion, style, speed, instruction,"
                "sample_rate, duration_s, total_ms, first_audio_ms, rtf,"
                "peak_vram_mb, engine, created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "rid1", "你好", "", "", 1.0, "",
                    24000, 0.4, 12.0, 12.0, 0.03,
                    0.0, "stub", "2026-01-01T00:00:00",
                ),
            )
        conn = db.connect()
        row = conn.execute("SELECT * FROM synthesis_logs").fetchone()
        assert row["request_id"] == "rid1"
        assert row["sample_rate"] == 24000
    finally:
        db.close()
