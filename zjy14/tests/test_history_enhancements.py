"""历史记录增强功能测试：归档日志、级别模糊匹配、导出、全文搜索、自动清理。

对应 2026-11-18 补齐的四个已知限制：
  1. 归档日志查询（原：只读当前文件）
  2. 日志级别模糊匹配（原：WARN 匹配不到 WARNING）
  3. 导出 CSV/JSON（原：无导出）
  4. 全文搜索索引（原：LIKE 无索引）
  5. 自动清理（原：需手动 DELETE）
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from custom_voice.history.cleanup import (
    CleanupScheduler,
    cutoff_iso,
    purge_archived_logs,
)
from custom_voice.history.logs import (
    LogQuery,
    log_file_path,
    query_logs,
)
from custom_voice.history.repository import (
    EXPORT_MAX_ROWS,
    QueryFilter,
    SynthesisHistoryRepository,
)

# ----------------------------------------------------------------------
# fixture
# ----------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_data_dir: Path) -> SynthesisHistoryRepository:
    from custom_voice.db.migrate import migrate
    from custom_voice.db.session import Database

    db = Database(tmp_data_dir / "test.db")
    migrate(db)
    yield SynthesisHistoryRepository(db)
    db.close()


def _insert(
    repo: SynthesisHistoryRepository,
    *,
    request_id: str = "r1",
    text: str = "测试文本",
    instruction: str = "",
    voice: str = "elysia",
    error: str = "",
    created_at: str = "2026-11-18T10:00:00+00:00",
) -> None:
    db = repo._db  # noqa: SLF001
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO synthesis_logs ("
            "request_id, speaker_id, text, emotion, style, speed, instruction,"
            "sample_rate, duration_s, total_ms, first_audio_ms, rtf,"
            "peak_vram_mb, engine, error_code, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                request_id, voice, text, "", "", 1.0, instruction,
                24000, 2.0, 1300.0, 1000.0, 0.65,
                3500.0, "cosyvoice3", error, created_at,
            ),
        )


# ----------------------------------------------------------------------
# 1. 归档日志查询
# ----------------------------------------------------------------------


def test_log_file_path_current(tmp_data_dir: Path) -> None:
    p = log_file_path()
    assert p.name == "zjy14.log"


def test_log_file_path_archive(tmp_data_dir: Path) -> None:
    p = log_file_path("zjy14.log.2026-11-16")
    assert p.name == "zjy14.log.2026-11-16"


def test_log_file_path_rejects_traversal(tmp_data_dir: Path) -> None:
    """路径穿越必须被拒绝。"""
    for bad in ("../etc/passwd", "zjy14.log/../../x", "a/b"):
        with pytest.raises(ValueError):
            log_file_path(bad)


def test_log_file_path_rejects_wrong_prefix(tmp_data_dir: Path) -> None:
    with pytest.raises(ValueError):
        log_file_path("other.log")


def _write_log(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )


def test_query_archive_file(tmp_data_dir: Path) -> None:
    """能查归档文件，而不是只能查当前日志。"""
    from custom_voice.core.paths import log_dir

    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)

    # 当前日志与归档各一份
    _write_log(d / "zjy14.log", [
        {"ts": "t1", "level": "INFO", "event": "当前日志"},
    ])
    _write_log(d / "zjy14.log.2026-11-16", [
        {"ts": "t2", "level": "ERROR", "event": "归档日志"},
    ])

    cur = query_logs(LogQuery())
    assert len(cur.entries) == 1
    assert cur.entries[0].event == "当前日志"

    arc = query_logs(LogQuery(file_name="zjy14.log.2026-11-16"))
    assert len(arc.entries) == 1
    assert arc.entries[0].event == "归档日志"
    assert "2026-11-16" in arc.path


def test_query_archive_missing_file(tmp_data_dir: Path) -> None:
    r = query_logs(LogQuery(file_name="zjy14.log.1999-01-01"))
    assert r.entries == []
    assert r.file_size == 0


# ----------------------------------------------------------------------
# 2. 日志级别模糊匹配
# ----------------------------------------------------------------------


@pytest.fixture()
def log_with_levels(tmp_data_dir: Path) -> Path:
    from custom_voice.core.paths import log_dir

    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / "zjy14.log"
    _write_log(p, [
        {"ts": "t1", "level": "INFO", "event": "普通"},
        {"ts": "t2", "level": "WARNING", "event": "警告"},
        {"ts": "t3", "level": "ERROR", "event": "错误"},
        {"ts": "t4", "level": "CRITICAL", "event": "严重"},
    ])
    return p


def test_level_exact_match(log_with_levels: Path) -> None:
    r = query_logs(LogQuery(levels=["WARNING"]))
    assert len(r.entries) == 1


def test_level_prefix_match_warn(log_with_levels: Path) -> None:
    """传 WARN 应匹配 WARNING —— 这是本次修复的缺陷。"""
    r = query_logs(LogQuery(levels=["WARN"]))
    assert len(r.entries) == 1
    assert r.entries[0].level == "WARNING"


def test_level_prefix_match_err(log_with_levels: Path) -> None:
    r = query_logs(LogQuery(levels=["ERR"]))
    assert len(r.entries) == 1
    assert r.entries[0].level == "ERROR"


def test_level_prefix_match_critical(log_with_levels: Path) -> None:
    r = query_logs(LogQuery(levels=["CRIT"]))
    assert len(r.entries) == 1


def test_level_lowercase_input(log_with_levels: Path) -> None:
    """小写输入也应能匹配。"""
    r = query_logs(LogQuery(levels=["warn"]))
    assert len(r.entries) == 1


def test_level_multiple_prefixes(log_with_levels: Path) -> None:
    r = query_logs(LogQuery(levels=["WARN", "ERR"]))
    assert len(r.entries) == 2


# ----------------------------------------------------------------------
# 3. 导出
# ----------------------------------------------------------------------


def test_export_csv(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", text="第一条")
    _insert(repo, request_id="b", text="第二条")

    body, mime, filename = repo.export(QueryFilter(), fmt="csv")
    assert "text/csv" in mime
    assert filename.endswith(".csv")

    text = body.decode("utf-8-sig")  # 去 BOM
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    assert len(rows) == 3  # 表头 + 2 行
    assert rows[0][0] == "id"
    assert "request_id" in rows[0]
    assert "text" in rows[0]


def test_export_csv_has_bom(repo: SynthesisHistoryRepository) -> None:
    """CSV 必须带 UTF-8 BOM，否则 Excel 打开中文乱码。"""
    _insert(repo, text="中文测试")
    body, _, _ = repo.export(QueryFilter(), fmt="csv")
    assert body.startswith(b"\xef\xbb\xbf")


def test_export_json(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="x", text="导出测试")
    body, mime, filename = repo.export(QueryFilter(), fmt="json")
    assert "json" in mime
    assert filename.endswith(".json")

    d = json.loads(body)
    assert d["count"] == 1
    assert d["items"][0]["request_id"] == "x"
    assert "exported_at" in d
    assert "filter" in d


def test_export_respects_filter(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", voice="elysia")
    _insert(repo, request_id="b", voice="other")

    body, _, _ = repo.export(QueryFilter(voice_id="elysia"), fmt="json")
    d = json.loads(body)
    assert d["count"] == 1
    assert d["items"][0]["request_id"] == "a"


def test_export_empty(repo: SynthesisHistoryRepository) -> None:
    body, _, _ = repo.export(QueryFilter(), fmt="json")
    d = json.loads(body)
    assert d["count"] == 0
    assert d["items"] == []


def test_export_max_rows(repo: SynthesisHistoryRepository) -> None:
    for i in range(10):
        _insert(repo, request_id=f"r{i}", created_at=f"2026-11-18T10:00:{i:02d}+00:00")

    body, _, _ = repo.export(QueryFilter(), fmt="json", max_rows=3)
    d = json.loads(body)
    assert d["count"] == 3


def test_export_paginates_internally(repo: SynthesisHistoryRepository) -> None:
    """导出应跨页取全量，而不是只取第一页。"""
    for i in range(250):  # 超过 MAX_PAGE_SIZE(200)
        _insert(repo, request_id=f"r{i}", created_at=f"2026-11-18T10:00:{i % 60:02d}+00:00")

    body, _, _ = repo.export(QueryFilter(), fmt="json", max_rows=300)
    d = json.loads(body)
    assert d["count"] == 250


def test_export_max_rows_constant() -> None:
    assert EXPORT_MAX_ROWS >= 10000


# ----------------------------------------------------------------------
# 4. 全文搜索
# ----------------------------------------------------------------------


def test_fts_index_exists(repo: SynthesisHistoryRepository) -> None:
    conn = repo._db.connect()  # noqa: SLF001
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "synthesis_fts" in tables


def test_fts_search_basic(repo: SynthesisHistoryRepository) -> None:
    """trigram 分词器要求 >= 3 字符，3 字查询走索引。"""
    _insert(repo, request_id="a", text="今天天气不错")
    _insert(repo, request_id="b", text="明天要下雨")

    page = repo.query(QueryFilter(text_search="今天天"))
    assert len(page.items) == 1
    assert page.items[0].request_id == "a"


def test_fts_search_two_chars_falls_back_to_like(
    repo: SynthesisHistoryRepository,
) -> None:
    """2 字查询在 trigram 下无效，必须自动降级到 LIKE 而不是返回空。

    这是实测发现的约束：trigram 要求 >= 3 字符。
    若不降级，「搜天气」会静默返回 0 条 —— 又是一种静默失效。
    """
    _insert(repo, request_id="a", text="今天天气不错")
    _insert(repo, request_id="b", text="明天要下雨")

    page = repo.query(QueryFilter(text_search="天气"))
    assert len(page.items) == 1, "2 字查询应降级 LIKE 并命中"
    assert page.items[0].request_id == "a"


def test_fts_search_one_char(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", text="今天天气不错")
    page = repo.query(QueryFilter(text_search="天"))
    assert len(page.items) == 1


def test_fts_search_no_match(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", text="今天天气不错")
    page = repo.query(QueryFilter(text_search="不存在的词"))
    assert page.items == []


def test_fts_search_special_chars(repo: SynthesisHistoryRepository) -> None:
    """FTS5 有特殊语法，用户输入含特殊字符不能报错。"""
    _insert(repo, request_id="a", text="C++ 和 C# 的区别")
    # 这些在 FTS5 里是特殊字符，必须被安全转义
    for q in ("C++", '"未闭合', "a AND b", "x*", "-neg", "NEAR(a b)"):
        page = repo.query(QueryFilter(text_search=q))
        assert isinstance(page.items, list)  # 不抛异常即通过


def test_fts_syncs_on_insert(repo: SynthesisHistoryRepository) -> None:
    """新插入的记录应自动进索引（靠触发器）。"""
    _insert(repo, request_id="new", text="刚插入的记录")
    page = repo.query(QueryFilter(text_search="刚插入的"))
    assert len(page.items) == 1


def test_fts_syncs_on_delete(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="del", text="将被删除的记录")
    repo.delete_before("2099-01-01T00:00:00+00:00")
    page = repo.query(QueryFilter(text_search="将被删除"))
    assert page.items == []


def test_fts_rebuild(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", text="重建索引测试")
    n = repo.rebuild_fts()
    assert n >= 1
    page = repo.query(QueryFilter(text_search="重建索"))
    assert len(page.items) == 1


def test_fts_search_instruction_field(repo: SynthesisHistoryRepository) -> None:
    """instruction 也进索引。"""
    _insert(repo, request_id="a", text="普通文本内容", instruction="温柔一点")
    page = repo.query(QueryFilter(text_search="温柔一"))
    assert len(page.items) == 1


def test_fts_preferred_over_like(repo: SynthesisHistoryRepository) -> None:
    """同时给 text_search 与 text_contains 时用 FTS。"""
    _insert(repo, request_id="a", text="天气不错哦")
    page = repo.query(QueryFilter(text_search="天气不", text_contains="天气不"))
    assert len(page.items) == 1


def test_fts_min_query_len_constant() -> None:
    from custom_voice.history.repository import FTS_MIN_QUERY_LEN

    assert FTS_MIN_QUERY_LEN == 3


# ----------------------------------------------------------------------
# 5. 清理审计
# ----------------------------------------------------------------------


def test_delete_before_writes_audit(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, created_at="2026-01-01T00:00:00+00:00")
    removed = repo.delete_before("2026-11-01T00:00:00+00:00", triggered_by="manual")
    assert removed == 1

    history = repo.cleanup_history()
    assert len(history) == 1
    assert history[0]["kind"] == "synthesis"
    assert history[0]["removed"] == 1
    assert history[0]["triggered_by"] == "manual"


def test_cleanup_history_order(repo: SynthesisHistoryRepository) -> None:
    repo.delete_before("2026-01-01T00:00:00+00:00")
    repo.delete_before("2026-02-01T00:00:00+00:00")
    h = repo.cleanup_history()
    assert len(h) == 2
    # 最新在前
    assert h[0]["cutoff"] > h[1]["cutoff"]


def test_cleanup_history_limit(repo: SynthesisHistoryRepository) -> None:
    for i in range(5):
        repo.delete_before(f"2026-0{i + 1}-01T00:00:00+00:00")
    assert len(repo.cleanup_history(limit=2)) == 2


# ----------------------------------------------------------------------
# 6. 自动清理调度器
# ----------------------------------------------------------------------


def test_cutoff_iso() -> None:
    c = cutoff_iso(30)
    dt = datetime.fromisoformat(c)
    delta = datetime.now(timezone.utc) - dt
    assert 29 <= delta.days <= 30


def test_cutoff_iso_zero() -> None:
    """0 天表示「现在」，用于清空全部。"""
    c = cutoff_iso(0)
    dt = datetime.fromisoformat(c)
    delta = datetime.now(timezone.utc) - dt
    assert delta.total_seconds() < 5


def test_purge_archived_logs(tmp_data_dir: Path) -> None:
    from custom_voice.core.paths import log_dir

    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)

    current = d / "zjy14.log"
    current.write_text("current\n", encoding="utf-8")

    old = d / "zjy14.log.2026-01-01"
    old.write_text("old\n", encoding="utf-8")
    # 把修改时间设为 100 天前
    old_ts = (datetime.now() - timedelta(days=100)).timestamp()
    os.utime(old, (old_ts, old_ts))

    recent = d / "zjy14.log.2026-11-17"
    recent.write_text("recent\n", encoding="utf-8")

    removed = purge_archived_logs(retain_days=14)
    assert "zjy14.log.2026-01-01" in removed
    assert not old.exists()
    # 当前日志与近期归档必须保留
    assert current.exists()
    assert recent.exists()


def test_purge_archived_logs_never_touches_current(tmp_data_dir: Path) -> None:
    """当前日志无论多旧都不能删。"""
    from custom_voice.core.paths import log_dir

    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    cur = d / "zjy14.log"
    cur.write_text("current\n", encoding="utf-8")
    old_ts = (datetime.now() - timedelta(days=999)).timestamp()
    os.utime(cur, (old_ts, old_ts))

    purge_archived_logs(retain_days=1)
    assert cur.exists()


def test_purge_archived_logs_zero_retain(tmp_data_dir: Path) -> None:
    """保留期为 0 表示不清理。"""
    from custom_voice.core.paths import log_dir

    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    old = d / "zjy14.log.2026-01-01"
    old.write_text("old\n", encoding="utf-8")

    removed = purge_archived_logs(retain_days=0)
    assert removed == []
    assert old.exists()


def test_cleanup_scheduler_run_once(repo: SynthesisHistoryRepository) -> None:
    db = repo._db  # noqa: SLF001
    _insert(repo, created_at="2020-01-01T00:00:00+00:00")

    sched = CleanupScheduler(db, retain_days=30, enabled=True)
    summary = sched.run_once(triggered_by="manual")

    assert summary["removed_records"] == 1
    assert summary["triggered_by"] == "manual"
    assert repo.count() == 0


def test_cleanup_scheduler_stats(repo: SynthesisHistoryRepository) -> None:
    db = repo._db  # noqa: SLF001
    sched = CleanupScheduler(db, retain_days=7, log_retain_days=3, interval_hours=12)
    s = sched.stats()
    assert s["enabled"] is True
    assert s["retain_days"] == 7
    assert s["log_retain_days"] == 3
    assert s["interval_h"] == 12.0
    assert s["runs"] == 0
    assert s["last_run"] is None


def test_cleanup_scheduler_disabled(repo: SynthesisHistoryRepository) -> None:
    db = repo._db  # noqa: SLF001
    _insert(repo, created_at="2020-01-01T00:00:00+00:00")
    sched = CleanupScheduler(db, retain_days=0, enabled=False)
    sched.start()  # 空操作
    s = sched.stats()
    assert s["enabled"] is False
    assert s["thread_alive"] is False
    # 数据仍在
    assert repo.count() == 1


def test_cleanup_scheduler_thread_lifecycle(repo: SynthesisHistoryRepository) -> None:
    """启动后线程存活，停止后退出。"""
    db = repo._db  # noqa: SLF001
    sched = CleanupScheduler(db, retain_days=30, interval_hours=24, enabled=True)
    sched.start()
    try:
        assert sched.stats()["thread_alive"] is True
    finally:
        sched.stop()
    assert sched.stats()["thread_alive"] is False


# ----------------------------------------------------------------------
# 7. HTTP 接口
# ----------------------------------------------------------------------


def test_http_export_csv(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/synthesize", json={"text": "导出测试文本", "voice_id": "elysia"})
    r = client.get("/api/v1/history/export?fmt=csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")


def test_http_export_json(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/synthesize", json={"text": "导出 JSON", "voice_id": "elysia"})
    r = client.get("/api/v1/history/export?fmt=json")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] >= 1
    assert "items" in d


def test_http_export_invalid_format(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/history/export?fmt=xml")
    assert r.status_code == 422


def test_http_export_route_not_shadowed(client) -> None:  # type: ignore[no-untyped-def]
    """/history/export 不能被 /history/{record_id} 吃掉。"""
    r = client.get("/api/v1/history/export?fmt=json")
    assert r.status_code == 200
    assert "items" in r.json()


def test_http_cleanup_history_route_not_shadowed(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/history/cleanup-history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_http_text_search(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/synthesize", json={"text": "全文搜索测试文本", "voice_id": "elysia"})
    # 用 >= 3 字符，走 FTS 索引
    r = client.get("/api/v1/history?text_search=全文搜")
    assert r.status_code == 200
    assert r.json()["count"] >= 1


def test_http_text_search_short_falls_back(client) -> None:  # type: ignore[no-untyped-def]
    """2 字查询通过 HTTP 也应命中（内部降级 LIKE）。"""
    client.post("/synthesize", json={"text": "天气真不错", "voice_id": "elysia"})
    r = client.get("/api/v1/history?text_search=天气")
    assert r.status_code == 200
    assert r.json()["count"] >= 1


def test_http_logs_level_prefix(client, log_with_levels: Path) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/logs?level=WARN")
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_http_logs_archive_file(client, tmp_data_dir: Path) -> None:  # type: ignore[no-untyped-def]
    from custom_voice.core.paths import log_dir

    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    _write_log(d / "zjy14.log.2026-11-15", [
        {"ts": "t", "level": "INFO", "event": "归档内容"},
    ])

    r = client.get("/api/v1/logs?file_name=zjy14.log.2026-11-15")
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_http_logs_archive_traversal_rejected(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/logs?file_name=../etc/passwd")
    assert r.status_code == 400


def test_http_cleanup_audit_after_purge(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/synthesize", json={"text": "待清理", "voice_id": "elysia"})
    client.delete("/api/v1/history?before=2099-01-01T00:00:00%2B00:00")

    r = client.get("/api/v1/history/cleanup-history")
    assert r.status_code == 200
    records = r.json()
    assert len(records) >= 1
    assert records[0]["kind"] == "synthesis"
    assert records[0]["removed"] >= 1


# ----------------------------------------------------------------------
# 8. 配置项
# ----------------------------------------------------------------------


def test_cleanup_config_defaults() -> None:
    from custom_voice.config.schema import GPUConfig

    f = GPUConfig.model_fields
    assert f["history_retain_days"].default == 30
    assert f["log_retain_days"].default == 14
    assert f["cleanup_interval_hours"].default == 24.0


def test_cleanup_config_registered() -> None:
    """新增配置必须在 CONSUMED_FIELDS 登记，否则启动报错。"""
    from custom_voice.config.schema import CONSUMED_FIELDS

    for name in ("history_retain_days", "log_retain_days", "cleanup_interval_hours"):
        assert name in CONSUMED_FIELDS
        assert "cleanup" in CONSUMED_FIELDS[name].lower()


def test_system_status_exposes_cleanup(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/system/status")
    assert r.status_code == 200
    d = r.json()
    assert "cleanup" in d
    assert d["cleanup"]["enabled"] in (True, False)
    assert "retain_days" in d["cleanup"]
