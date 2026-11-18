"""四个小缺口的补齐测试。

对应 2026-11-18 第二次补齐：
  1. 日志跨文件搜索（原来需指定单个文件）
  2. 全文搜索按相关度排序（原来只按时间倒序）
  3. 导出进度反馈（原来同步返回，无进度）
  4. 日志清理审计（原来只审计合成记录）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_voice.history.logs import LogQuery, query_logs
from custom_voice.history.repository import (
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
                request_id, "elysia", text, "", "", 1.0, "",
                24000, 2.0, 1300.0, 1000.0, 0.65,
                3500.0, "cosyvoice3", "", created_at,
            ),
        )


def _write_log(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )


# ----------------------------------------------------------------------
# 1. 日志跨文件搜索
# ----------------------------------------------------------------------


@pytest.fixture()
def multi_log_files(tmp_data_dir: Path) -> Path:
    """造当前日志 + 2 个归档。"""
    from custom_voice.core.paths import log_dir

    d = log_dir()
    _write_log(d / "zjy14.log", [
        {"ts": "t1", "level": "INFO", "event": "当前日志的第一条"},
        {"ts": "t2", "level": "ERROR", "event": "当前日志的错误"},
    ])
    _write_log(d / "zjy14.log.2026-11-16", [
        {"ts": "t3", "level": "INFO", "event": "归档一的内容"},
    ])
    _write_log(d / "zjy14.log.2026-11-15", [
        {"ts": "t4", "level": "WARNING", "event": "归档二的警告"},
    ])
    return d


def test_cross_file_star(multi_log_files: Path) -> None:
    """file_name='*' 应跨全部文件搜索。"""
    r = query_logs(LogQuery(file_name="*", limit=100))
    assert len(r.entries) == 4
    assert "4 个文件" in r.path or "文件" in r.path


def test_cross_file_all(multi_log_files: Path) -> None:
    """file_name='all' 等价于 '*'。"""
    r = query_logs(LogQuery(file_name="all", limit=100))
    assert len(r.entries) == 4


def test_cross_file_has_source(multi_log_files: Path) -> None:
    """跨文件结果必须带 source_file，否则不知道是哪天的日志。"""
    r = query_logs(LogQuery(file_name="*", limit=100))
    assert all(e.source_file for e in r.entries)
    names = {e.source_file for e in r.entries}
    assert "zjy14.log" in names
    assert "zjy14.log.2026-11-16" in names


def test_cross_file_source_in_dict(multi_log_files: Path) -> None:
    r = query_logs(LogQuery(file_name="*", limit=100))
    d = r.entries[0].to_dict()
    assert "source_file" in d


def test_cross_file_respects_limit(multi_log_files: Path) -> None:
    r = query_logs(LogQuery(file_name="*", limit=2))
    assert len(r.entries) == 2
    assert r.has_more is True


def test_cross_file_respects_level(multi_log_files: Path) -> None:
    r = query_logs(LogQuery(file_name="*", levels=["ERROR"], limit=100))
    assert len(r.entries) == 1
    assert r.entries[0].event == "当前日志的错误"


def test_cross_file_respects_keyword(multi_log_files: Path) -> None:
    r = query_logs(LogQuery(file_name="*", keyword="归档", limit=100))
    assert len(r.entries) == 2
    assert all("归档" in e.event for e in r.entries)


def test_cross_file_current_first(multi_log_files: Path) -> None:
    """当前日志应排在最前（它最新）。"""
    r = query_logs(LogQuery(file_name="*", limit=100))
    assert r.entries[0].source_file == "zjy14.log"


def test_cross_file_empty_dir(tmp_data_dir: Path) -> None:
    r = query_logs(LogQuery(file_name="*"))
    assert r.entries == []


def test_http_cross_file(client, multi_log_files: Path) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/logs?file_name=*&limit=100")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 4
    assert all("source_file" in e for e in d["entries"])


def test_http_cross_file_all(client, multi_log_files: Path) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/logs?file_name=all&limit=100")
    assert r.status_code == 200
    assert r.json()["count"] == 4


# ----------------------------------------------------------------------
# 2. 全文搜索相关度排序
# ----------------------------------------------------------------------


def test_relevance_order_most_relevant_first(
    repo: SynthesisHistoryRepository,
) -> None:
    """相关度排序：bm25 分数低的排前面。

    实测规律（probe/_diag_bm25.py）：
      bm25 有长度归一化，短文档得分更高（更负）。
      「天气天气天气天好」(8字) 得分 -0.000002
      「天气天 + 无关内容×10」(43字) 得分 -0.000001
    因此短密集的应排前。

    注意两点：
      1. 必须用 >= 3 字查询，否则 trigram 不命中，relevance 回退时间排序
      2. 两条记录都必须**真的包含查询词**，否则只命中一条，无法比较
    """
    # 短文档（8 字）
    _insert(repo, request_id="short",
            text="天气天气天气天好",
            created_at="2026-11-18T09:00:00+00:00")
    # 长文档（43 字），时间更晚
    _insert(repo, request_id="long",
            text="天气天" + "无关内容" * 10,
            created_at="2026-11-18T12:00:00+00:00")

    by_time = repo.query(QueryFilter(text_search="天气天", order_by="time"))
    assert len(by_time.items) == 2, (
        f"两条都应命中，实际 {[i.request_id for i in by_time.items]}"
    )
    # 时间排序：long 更新（12:00 > 09:00），排前
    assert by_time.items[0].request_id == "long"

    # 相关度排序：bm25 长度归一化让短文档得分更高，short 应排前
    by_rel = repo.query(QueryFilter(text_search="天气天", order_by="relevance"))
    assert len(by_rel.items) == 2
    assert by_rel.items[0].request_id == "short", (
        "bm25 的长度归一化应让短文档排前，实际顺序："
        f"{[i.request_id for i in by_rel.items]}"
    )


def test_relevance_falls_back_without_search(
    repo: SynthesisHistoryRepository,
) -> None:
    """没有 text_search 时，relevance 自动回退到时间排序。"""
    _insert(repo, request_id="a", created_at="2026-11-18T09:00:00+00:00")
    _insert(repo, request_id="b", created_at="2026-11-18T11:00:00+00:00")

    page = repo.query(QueryFilter(order_by="relevance"))
    assert [i.request_id for i in page.items] == ["b", "a"]


def test_relevance_falls_back_short_query(
    repo: SynthesisHistoryRepository,
) -> None:
    """2 字查询走 LIKE，没有相关度，回退时间排序。"""
    _insert(repo, request_id="a", text="天气不错", created_at="2026-11-18T09:00:00+00:00")
    _insert(repo, request_id="b", text="天气真好", created_at="2026-11-18T11:00:00+00:00")

    page = repo.query(QueryFilter(text_search="天气", order_by="relevance"))
    assert [i.request_id for i in page.items] == ["b", "a"]


def test_relevance_no_cursor(repo: SynthesisHistoryRepository) -> None:
    """相关度排序下不应返回时间游标（顺序与时间无关）。

    因为游标基于 (created_at, id)，而相关度排序的顺序与之无关，
    返回游标会让调用方翻页时得到错误结果。
    """
    for i in range(5):
        _insert(repo, request_id=f"r{i}", text="天气不错啊",
                created_at=f"2026-11-18T10:00:{i:02d}+00:00")

    page = repo.query(QueryFilter(text_search="天气不", order_by="relevance", limit=2))
    assert page.has_more is True, "应提示还有更多"
    assert page.next_cursor is None, "相关度排序不应给时间游标"


def test_relevance_with_filter(repo: SynthesisHistoryRepository) -> None:
    """相关度排序 + 其它筛选条件应能组合。"""
    _insert(repo, request_id="ok", text="天气不错")
    _insert(repo, request_id="bad", text="天气糟糕")

    page = repo.query(
        QueryFilter(text_search="天气", order_by="relevance", only_success=True)
    )
    assert len(page.items) == 2


def test_http_order_by_relevance(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/synthesize", json={"text": "相关度排序测试天气", "voice_id": "elysia"})
    r = client.get("/api/v1/history?text_search=相关度&order_by=relevance")
    assert r.status_code == 200
    assert r.json()["count"] >= 1


def test_http_order_by_invalid(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/history?order_by=bogus")
    assert r.status_code == 422


# ----------------------------------------------------------------------
# 3. 导出流式与进度
# ----------------------------------------------------------------------


def test_iter_export_csv(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", text="第一条")
    _insert(repo, request_id="b", text="第二条")

    chunks = list(repo.iter_export(QueryFilter(), fmt="csv"))
    assert len(chunks) >= 2, "流式导出应分多块产出"
    full = "".join(chunks)
    assert full.startswith("\ufeff"), "CSV 必须以 BOM 开头"
    assert "request_id" in full
    assert "第一条" in full


def test_iter_export_json(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="x", text="JSON 流式")
    chunks = list(repo.iter_export(QueryFilter(), fmt="json"))
    full = "".join(chunks)
    d = json.loads(full)
    assert d["count"] == 1
    assert d["items"][0]["request_id"] == "x"


def test_iter_export_json_multiple_rows(
    repo: SynthesisHistoryRepository,
) -> None:
    """多条记录时 JSON 的逗号分隔必须正确（否则解析失败）。"""
    for i in range(5):
        _insert(repo, request_id=f"r{i}", created_at=f"2026-11-18T10:00:{i:02d}+00:00")

    full = "".join(repo.iter_export(QueryFilter(), fmt="json"))
    d = json.loads(full)
    assert d["count"] == 5
    assert len(d["items"]) == 5


def test_iter_export_json_empty(repo: SynthesisHistoryRepository) -> None:
    full = "".join(repo.iter_export(QueryFilter(), fmt="json"))
    d = json.loads(full)
    assert d["count"] == 0
    assert d["items"] == []


def test_iter_export_respects_max_rows(repo: SynthesisHistoryRepository) -> None:
    for i in range(10):
        _insert(repo, request_id=f"r{i}", created_at=f"2026-11-18T10:00:{i:02d}+00:00")
    full = "".join(repo.iter_export(QueryFilter(), fmt="json", max_rows=3))
    d = json.loads(full)
    assert d["count"] == 3


def test_iter_export_pages_internally(repo: SynthesisHistoryRepository) -> None:
    """应跨页取全量，不是只取一页。"""
    for i in range(250):  # 超过 MAX_PAGE_SIZE(200)
        _insert(repo, request_id=f"r{i}",
                created_at=f"2026-11-18T10:{i % 60:02d}:00+00:00")
    full = "".join(repo.iter_export(QueryFilter(), fmt="json", max_rows=300))
    d = json.loads(full)
    assert d["count"] == 250


def test_export_equals_iter_export(repo: SynthesisHistoryRepository) -> None:
    """export() 应与 iter_export() 拼起来等价。"""
    _insert(repo, request_id="a", text="一致性测试")
    body, _, _ = repo.export(QueryFilter(), fmt="json")
    full = "".join(repo.iter_export(QueryFilter(), fmt="json"))
    assert json.loads(body)["count"] == json.loads(full)["count"]


def test_count_for_export(repo: SynthesisHistoryRepository) -> None:
    for i in range(7):
        _insert(repo, request_id=f"r{i}")
    assert repo.count_for_export(QueryFilter()) == 7


def test_count_for_export_with_filter(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", text="天气不错")
    _insert(repo, request_id="b", text="无关内容")
    assert repo.count_for_export(QueryFilter(text_search="天气")) == 1


def test_http_export_total_header(client) -> None:  # type: ignore[no-untyped-def]
    """导出响应必须带 x-export-total，前端据此显示进度。"""
    for i in range(3):
        client.post("/synthesize", json={"text": f"导出进度测试{i}", "voice_id": "elysia"})

    r = client.get("/api/v1/history/export?fmt=csv")
    assert r.status_code == 200
    assert "x-export-total" in r.headers
    total = int(r.headers["x-export-total"])
    assert total >= 3
    assert "x-export-limit" in r.headers


def test_http_export_streaming_works(client) -> None:  # type: ignore[no-untyped-def]
    """流式响应内容应完整可解析。"""
    for i in range(5):
        client.post("/synthesize", json={"text": f"流式导出{i}", "voice_id": "elysia"})

    r = client.get("/api/v1/history/export?fmt=json")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] >= 5


# ----------------------------------------------------------------------
# 4. 日志清理审计
# ----------------------------------------------------------------------


def test_record_cleanup_logs_kind(repo: SynthesisHistoryRepository) -> None:
    """日志文件清理也要写审计。"""
    repo.record_cleanup(
        kind="logs",
        cutoff="2026-11-01T00:00:00+00:00",
        removed=2,
        triggered_by="scheduled",
        detail={"files": ["zjy14.log.2026-10-01", "zjy14.log.2026-10-02"]},
    )

    history = repo.cleanup_history()
    assert len(history) == 1
    rec = history[0]
    assert rec["kind"] == "logs"
    assert rec["removed"] == 2
    assert rec["triggered_by"] == "scheduled"
    assert rec["detail"]["files"] == [
        "zjy14.log.2026-10-01", "zjy14.log.2026-10-02"
    ]


def test_cleanup_history_parses_detail(repo: SynthesisHistoryRepository) -> None:
    """detail 应被解析成 dict，而不是返回原始 JSON 字符串。"""
    repo.record_cleanup(
        kind="logs", cutoff="c", removed=1, detail={"files": ["a.log"]}
    )
    rec = repo.cleanup_history()[0]
    assert isinstance(rec["detail"], dict)
    assert rec["detail"]["files"] == ["a.log"]


def test_cleanup_history_empty_detail(repo: SynthesisHistoryRepository) -> None:
    """没有 detail 时返回空 dict，不是空字符串。"""
    repo.delete_before("2026-01-01T00:00:00+00:00")
    rec = repo.cleanup_history()[0]
    assert rec["detail"] == {}


def test_cleanup_history_both_kinds(repo: SynthesisHistoryRepository) -> None:
    repo.delete_before("2026-01-01T00:00:00+00:00", triggered_by="manual")
    repo.record_cleanup(kind="logs", cutoff="c", removed=3, detail={"files": []})

    h = repo.cleanup_history()
    assert len(h) == 2
    kinds = {r["kind"] for r in h}
    assert kinds == {"synthesis", "logs"}


def test_scheduler_audits_log_files(repo: SynthesisHistoryRepository,
                                    tmp_data_dir: Path) -> None:
    """调度器删除归档日志时必须写审计。"""
    import os
    from datetime import datetime, timedelta

    from custom_voice.core.paths import log_dir
    from custom_voice.history.cleanup import CleanupScheduler

    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    old = d / "zjy14.log.2020-01-01"
    old.write_text("old\n", encoding="utf-8")
    old_ts = (datetime.now() - timedelta(days=999)).timestamp()
    os.utime(old, (old_ts, old_ts))

    sched = CleanupScheduler(
        repo._db,  # noqa: SLF001
        retain_days=30,
        log_retain_days=1,
        enabled=True,
    )
    summary = sched.run_once(triggered_by="scheduled")
    assert summary["removed_log_files"] == ["zjy14.log.2020-01-01"]

    h = repo.cleanup_history()
    logs_rec = [r for r in h if r["kind"] == "logs"]
    assert len(logs_rec) == 1
    assert logs_rec[0]["removed"] == 1
    assert "zjy14.log.2020-01-01" in logs_rec[0]["detail"]["files"]


def test_http_cleanup_detail(client) -> None:  # type: ignore[no-untyped-def]
    """HTTP 返回的审计记录应含 detail 字段。"""
    client.post("/synthesize", json={"text": "审计测试", "voice_id": "elysia"})
    client.delete("/api/v1/history?before=2099-01-01T00:00:00%2B00:00")

    r = client.get("/api/v1/history/cleanup-history")
    assert r.status_code == 200
    records = r.json()
    assert len(records) >= 1
    assert "detail" in records[0]
    assert isinstance(records[0]["detail"], dict)


# ----------------------------------------------------------------------
# 5. 数据库迁移（v3 -> v4 加列）
# ----------------------------------------------------------------------


def test_schema_version_is_4() -> None:
    from custom_voice.db.models import SCHEMA_VERSION

    assert SCHEMA_VERSION >= 4


def test_alter_statements_defined() -> None:
    from custom_voice.db.models import ALTER_STATEMENTS

    assert len(ALTER_STATEMENTS) >= 1
    versions = [v for v, _ in ALTER_STATEMENTS]
    assert 4 in versions


def test_migrate_is_idempotent(tmp_data_dir: Path) -> None:
    """重复迁移不应报错（ALTER 会撞 duplicate column）。"""
    from custom_voice.db.migrate import current_version, migrate
    from custom_voice.db.session import Database

    db = Database(tmp_data_dir / "m.db")
    try:
        v1 = migrate(db)
        v2 = migrate(db)
        v3 = migrate(db)
        assert v1 == v2 == v3
        assert current_version(db) == v1
    finally:
        db.close()


def test_migrate_from_v3_adds_detail_column(tmp_data_dir: Path) -> None:
    """模拟从 v3 升级：cleanup_logs 表已存在但没有 detail 列。"""
    from custom_voice.db.migrate import migrate
    from custom_voice.db.session import Database

    db = Database(tmp_data_dir / "old.db")
    conn = db.connect()
    # 手工造一个 v3 状态的库（cleanup_logs 无 detail）
    conn.executescript("""
        CREATE TABLE cleanup_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            cutoff TEXT NOT NULL,
            removed INTEGER NOT NULL,
            triggered_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
        );
        INSERT INTO schema_version VALUES (3, '2026-11-01T00:00:00+00:00');
    """)

    try:
        v = migrate(db)
        assert v >= 4

        # detail 列应已存在
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(cleanup_logs)")]
        assert "detail" in cols
    finally:
        db.close()


def test_detail_column_default_empty(tmp_data_dir: Path) -> None:
    """新加的 detail 列对旧数据默认空串，不应为 NULL。"""
    from custom_voice.db.migrate import migrate
    from custom_voice.db.session import Database

    db = Database(tmp_data_dir / "d.db")
    try:
        migrate(db)
        conn = db.connect()
        conn.execute(
            "INSERT INTO cleanup_logs (kind, cutoff, removed, created_at)"
            " VALUES ('synthesis', 'c', 0, 't')"
        )
        row = conn.execute(
            "SELECT detail FROM cleanup_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row["detail"] == ""
    finally:
        db.close()
