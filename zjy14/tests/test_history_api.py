"""合成记录与日志查询 API 测试。

覆盖：
- 游标编解码（含非法格式）
- 仓储查询：筛选、分页、边界
- 统计：成功/失败、RTF、分组
- 清理
- HTTP 接口：参数校验、404、路由顺序
- 日志解析：坏行容忍、反向读取、过滤
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_voice.history.logs import (
    LogQuery,
    available_log_files,
    log_file_path,
    query_logs,
)
from custom_voice.history.repository import (
    MAX_PAGE_SIZE,
    QueryFilter,
    SynthesisHistoryRepository,
    decode_cursor,
    encode_cursor,
)

# ----------------------------------------------------------------------
# 游标
# ----------------------------------------------------------------------


def test_cursor_roundtrip() -> None:
    c = encode_cursor("2026-11-16T10:00:00+08:00", 42)
    t, i = decode_cursor(c)
    assert t == "2026-11-16T10:00:00+08:00"
    assert i == 42


def test_cursor_is_opaque() -> None:
    """游标不应直接暴露内部格式（避免调用方依赖）。"""
    c = encode_cursor("2026-11-16T10:00:00+08:00", 42)
    assert "2026" not in c
    assert ":" not in c


def test_cursor_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        decode_cursor("not-a-valid-cursor")


def test_cursor_rejects_empty() -> None:
    with pytest.raises(ValueError):
        decode_cursor("")


# ----------------------------------------------------------------------
# 仓储：查询
# ----------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_data_dir: Path) -> SynthesisHistoryRepository:
    """带建表的仓储。"""
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
    voice: str = "elysia",
    emotion: str = "",
    error: str = "",
    duration: float = 2.0,
    total_ms: float = 1300.0,
    rtf: float = 0.65,
    created_at: str = "2026-11-16T10:00:00+00:00",
) -> None:
    """直接插一条记录（绕过 HTTP，便于控制时间）。"""
    db = repo._db  # noqa: SLF001
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO synthesis_logs ("
            "request_id, speaker_id, text, emotion, style, speed, instruction,"
            "sample_rate, duration_s, total_ms, first_audio_ms, rtf,"
            "peak_vram_mb, engine, error_code, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                request_id, voice, text, emotion, "", 1.0, "",
                24000, duration, total_ms, 1000.0, rtf,
                3500.0, "cosyvoice3", error, created_at,
            ),
        )


def test_query_empty(repo: SynthesisHistoryRepository) -> None:
    page = repo.query(QueryFilter())
    assert page.items == []
    assert page.has_more is False
    assert page.next_cursor is None


def test_query_returns_newest_first(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="old", created_at="2026-11-16T09:00:00+00:00")
    _insert(repo, request_id="new", created_at="2026-11-16T11:00:00+00:00")
    page = repo.query(QueryFilter())
    assert [i.request_id for i in page.items] == ["new", "old"]


def test_query_filter_by_voice(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", voice="elysia")
    _insert(repo, request_id="b", voice="other")
    page = repo.query(QueryFilter(voice_id="elysia"))
    assert len(page.items) == 1
    assert page.items[0].request_id == "a"


def test_query_filter_only_failed(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="ok", error="")
    _insert(repo, request_id="bad", error="synthesis_error")
    page = repo.query(QueryFilter(only_failed=True))
    assert len(page.items) == 1
    assert page.items[0].request_id == "bad"


def test_query_filter_only_success(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="ok", error="")
    _insert(repo, request_id="bad", error="synthesis_error")
    page = repo.query(QueryFilter(only_success=True))
    assert len(page.items) == 1
    assert page.items[0].request_id == "ok"


def test_query_filter_text_contains(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", text="今天天气不错")
    _insert(repo, request_id="b", text="明天要下雨")
    page = repo.query(QueryFilter(text_contains="天气"))
    assert len(page.items) == 1
    assert page.items[0].request_id == "a"


def test_query_filter_time_range(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="early", created_at="2026-11-16T08:00:00+00:00")
    _insert(repo, request_id="mid", created_at="2026-11-16T10:00:00+00:00")
    _insert(repo, request_id="late", created_at="2026-11-16T12:00:00+00:00")

    page = repo.query(
        QueryFilter(since="2026-11-16T09:00:00+00:00", until="2026-11-16T11:00:00+00:00")
    )
    assert [i.request_id for i in page.items] == ["mid"]


def test_query_filter_emotion(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", emotion="happy")
    _insert(repo, request_id="b", emotion="sad")
    page = repo.query(QueryFilter(emotion="happy"))
    assert len(page.items) == 1


# ----------------------------------------------------------------------
# 仓储：分页
# ----------------------------------------------------------------------


def test_pagination_no_duplicates(repo: SynthesisHistoryRepository) -> None:
    """翻页不应重复或跳过记录。这是游标分页的核心保证。"""
    for i in range(10):
        _insert(repo, request_id=f"r{i}", created_at=f"2026-11-16T10:00:{i:02d}+00:00")

    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = repo.query(QueryFilter(limit=3, cursor=cursor))
        seen.extend(i.request_id for i in page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor

    assert len(seen) == 10
    assert len(set(seen)) == 10, "翻页出现重复记录"
    # 应为倒序
    assert seen == [f"r{i}" for i in range(9, -1, -1)]


def test_pagination_has_more_flag(repo: SynthesisHistoryRepository) -> None:
    for i in range(5):
        _insert(repo, request_id=f"r{i}", created_at=f"2026-11-16T10:00:{i:02d}+00:00")

    page1 = repo.query(QueryFilter(limit=2))
    assert page1.has_more is True
    assert page1.next_cursor is not None

    page2 = repo.query(QueryFilter(limit=2, cursor=page1.next_cursor))
    assert len(page2.items) == 2


def test_pagination_last_page_no_cursor(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="only")
    page = repo.query(QueryFilter(limit=10))
    assert page.has_more is False
    assert page.next_cursor is None


def test_pagination_same_timestamp(repo: SynthesisHistoryRepository) -> None:
    """同一秒内多条记录时，靠 id 保证游标不重复。"""
    same = "2026-11-16T10:00:00+00:00"
    for i in range(5):
        _insert(repo, request_id=f"r{i}", created_at=same)

    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = repo.query(QueryFilter(limit=2, cursor=cursor))
        seen.extend(i.request_id for i in page.items)
        if not page.has_more:
            break
        cursor = page.next_cursor

    assert len(seen) == 5
    assert len(set(seen)) == 5


def test_limit_clamped_to_max(repo: SynthesisHistoryRepository) -> None:
    """超过上限时按上限截断，不报错。"""
    for i in range(3):
        _insert(repo, request_id=f"r{i}")
    page = repo.query(QueryFilter(limit=MAX_PAGE_SIZE + 999))
    assert len(page.items) <= MAX_PAGE_SIZE


def test_invalid_cursor_raises(repo: SynthesisHistoryRepository) -> None:
    with pytest.raises(ValueError):
        repo.query(QueryFilter(cursor="garbage"))


# ----------------------------------------------------------------------
# 仓储：单条查询
# ----------------------------------------------------------------------


def test_get_by_id(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="x")
    page = repo.query(QueryFilter())
    rec = repo.get(page.items[0].id)
    assert rec is not None
    assert rec.request_id == "x"


def test_get_missing_returns_none(repo: SynthesisHistoryRepository) -> None:
    assert repo.get(99999) is None


def test_get_by_request_id(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="dup", created_at="2026-11-16T09:00:00+00:00")
    _insert(repo, request_id="dup", created_at="2026-11-16T10:00:00+00:00")
    rows = repo.get_by_request_id("dup")
    assert len(rows) == 2


def test_get_by_request_id_empty(repo: SynthesisHistoryRepository) -> None:
    assert repo.get_by_request_id("nope") == []


# ----------------------------------------------------------------------
# 仓储：统计
# ----------------------------------------------------------------------


def test_stats_empty(repo: SynthesisHistoryRepository) -> None:
    s = repo.stats()
    assert s.total == 0
    assert s.success == 0
    assert s.failed == 0
    d = s.to_dict()
    assert d["success_rate"] == 0.0


def test_stats_counts(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a")
    _insert(repo, request_id="b")
    _insert(repo, request_id="c", error="synthesis_error")

    s = repo.stats()
    assert s.total == 3
    assert s.success == 2
    assert s.failed == 1
    assert s.to_dict()["success_rate"] == pytest.approx(0.6667, abs=1e-3)


def test_stats_totals(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", duration=2.0, total_ms=1300.0)
    _insert(repo, request_id="b", duration=3.0, total_ms=2000.0)

    s = repo.stats()
    assert s.total_audio_s == pytest.approx(5.0)
    assert s.total_synth_s == pytest.approx(3.3)


def test_stats_rtf(repo: SynthesisHistoryRepository) -> None:
    for rtf in (0.6, 0.7, 0.8):
        _insert(repo, request_id=f"r{rtf}", rtf=rtf)
    s = repo.stats()
    assert s.rtf_median == pytest.approx(0.7)
    assert s.rtf_min == pytest.approx(0.6)
    assert s.rtf_max == pytest.approx(0.8)


def test_stats_by_voice(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", voice="elysia")
    _insert(repo, request_id="b", voice="elysia")
    _insert(repo, request_id="c", voice="other")
    s = repo.stats()
    assert s.by_voice["elysia"] == 2
    assert s.by_voice["other"] == 1


def test_stats_by_error(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", error="synthesis_error")
    _insert(repo, request_id="b", error="synthesis_error")
    _insert(repo, request_id="c", error="vram_insufficient")
    s = repo.stats()
    assert s.by_error["synthesis_error"] == 2
    assert s.by_error["vram_insufficient"] == 1


def test_stats_respects_filter(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", voice="elysia")
    _insert(repo, request_id="b", voice="other")
    s = repo.stats(QueryFilter(voice_id="elysia"))
    assert s.total == 1


def test_stats_time_range(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", created_at="2026-11-16T08:00:00+00:00")
    _insert(repo, request_id="b", created_at="2026-11-16T12:00:00+00:00")
    s = repo.stats()
    assert s.first_at == "2026-11-16T08:00:00+00:00"
    assert s.last_at == "2026-11-16T12:00:00+00:00"


# ----------------------------------------------------------------------
# 仓储：清理
# ----------------------------------------------------------------------


def test_delete_before(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="old", created_at="2026-11-01T00:00:00+00:00")
    _insert(repo, request_id="new", created_at="2026-11-16T00:00:00+00:00")

    removed = repo.delete_before("2026-11-10T00:00:00+00:00")
    assert removed == 1
    assert repo.count() == 1
    assert repo.query(QueryFilter()).items[0].request_id == "new"


def test_delete_before_nothing(repo: SynthesisHistoryRepository) -> None:
    _insert(repo, request_id="a", created_at="2026-11-16T00:00:00+00:00")
    removed = repo.delete_before("2026-01-01T00:00:00+00:00")
    assert removed == 0
    assert repo.count() == 1


# ----------------------------------------------------------------------
# HTTP 接口
# ----------------------------------------------------------------------


def test_http_history_empty(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/history")
    assert r.status_code == 200
    d = r.json()
    assert d["items"] == []
    assert d["has_more"] is False


def test_http_history_after_synthesis(client) -> None:  # type: ignore[no-untyped-def]
    """合成一次后应能查到记录。"""
    r = client.post("/synthesize", json={"text": "你好世界", "voice_id": "elysia"})
    assert r.status_code == 200

    r2 = client.get("/api/v1/history")
    assert r2.status_code == 200
    items = r2.json()["items"]
    assert len(items) >= 1
    rec = items[0]
    assert rec["text"] == "你好世界"
    assert rec["voice_id"] == "elysia"
    assert rec["error_code"] == ""


def test_http_history_stats(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/synthesize", json={"text": "你好世界", "voice_id": "elysia"})
    r = client.get("/api/v1/history/stats")
    assert r.status_code == 200
    d = r.json()
    assert d["total"] >= 1
    assert d["success"] >= 1
    assert d["success_rate"] > 0


def test_http_history_stats_route_not_shadowed(client) -> None:  # type: ignore[no-untyped-def]
    """``/history/stats`` 不能被 ``/history/{record_id}`` 吃掉。

    这是 FastAPI 按注册顺序匹配的经典陷阱，必须有测试守护。
    """
    r = client.get("/api/v1/history/stats")
    assert r.status_code == 200
    assert "total" in r.json()


def test_http_history_detail_404(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/history/999999")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"


def test_http_history_detail(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/synthesize", json={"text": "详情测试", "voice_id": "elysia"})
    items = client.get("/api/v1/history").json()["items"]
    rid = items[0]["id"]

    r = client.get(f"/api/v1/history/{rid}")
    assert r.status_code == 200
    assert r.json()["id"] == rid


def test_http_history_by_request(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.post("/synthesize", json={"text": "按请求查", "voice_id": "elysia"})
    req_id = resp.headers.get("x-request-id")
    assert req_id

    r = client.get(f"/api/v1/history/by-request/{req_id}")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_http_history_only_failed_conflict(client) -> None:  # type: ignore[no-untyped-def]
    """only_failed 与 only_success 互斥。"""
    r = client.get("/api/v1/history?only_failed=true&only_success=true")
    assert r.status_code == 400
    assert "不能同时" in r.json()["message"]


def test_http_history_invalid_cursor(client) -> None:  # type: ignore[no-untyped-def]
    """非法游标应返回 400，而不是 500。"""
    r = client.get("/api/v1/history?cursor=garbage")
    assert r.status_code == 400


def test_http_history_limit_validation(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/history?limit=0")
    assert r.status_code == 422  # Pydantic 校验失败

    r2 = client.get(f"/api/v1/history?limit={MAX_PAGE_SIZE + 1}")
    assert r2.status_code == 422


def test_http_history_purge(client) -> None:  # type: ignore[no-untyped-def]
    client.post("/synthesize", json={"text": "待清理", "voice_id": "elysia"})
    r = client.delete("/api/v1/history?before=2099-01-01T00:00:00+00:00")
    assert r.status_code == 204
    # 清理后应为空
    assert client.get("/api/v1/history").json()["items"] == []


def test_http_history_purge_requires_before(client) -> None:  # type: ignore[no-untyped-def]
    r = client.delete("/api/v1/history")
    assert r.status_code == 422  # 缺少必填参数


# ----------------------------------------------------------------------
# 日志读取
# ----------------------------------------------------------------------


@pytest.fixture()
def log_file(tmp_data_dir: Path) -> Path:
    """在临时数据目录里造一份日志。"""
    from custom_voice.core.paths import log_dir

    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / "zjy14.log"
    lines = [
        {"ts": "2026-11-16T10:00:00+08:00", "level": "INFO",
         "logger": "zjy14.routes.synthesis", "event": "合成完成",
         "request_id": "req-1", "voice_id": "elysia", "duration_s": 2.5},
        {"ts": "2026-11-16T10:00:01+08:00", "level": "WARNING",
         "logger": "zjy14.engine.cosyvoice", "event": "合成文本过短",
         "request_id": "req-2", "ratio": 0.19},
        {"ts": "2026-11-16T10:00:02+08:00", "level": "ERROR",
         "logger": "zjy14.routes.synthesis", "event": "请求失败",
         "request_id": "req-3", "code": "synthesis_error",
         "exc": "Traceback (most recent call last):\n..."},
    ]
    p.write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
        encoding="utf-8",
    )
    return p


def test_log_file_path(tmp_data_dir: Path) -> None:
    p = log_file_path()
    assert p.name == "zjy14.log"
    assert str(tmp_data_dir) in str(p)


def test_query_logs_all(log_file: Path) -> None:
    r = query_logs(LogQuery())
    assert len(r.entries) == 3
    # 最新在前
    assert r.entries[0].level == "ERROR"
    assert r.entries[-1].level == "INFO"


def test_query_logs_limit(log_file: Path) -> None:
    r = query_logs(LogQuery(limit=2))
    assert len(r.entries) == 2
    assert r.has_more is True


def test_query_logs_filter_level(log_file: Path) -> None:
    r = query_logs(LogQuery(levels=["ERROR"]))
    assert len(r.entries) == 1
    assert r.entries[0].level == "ERROR"


def test_query_logs_filter_multiple_levels(log_file: Path) -> None:
    r = query_logs(LogQuery(levels=["WARNING", "ERROR"]))
    assert len(r.entries) == 2


def test_query_logs_filter_request_id(log_file: Path) -> None:
    r = query_logs(LogQuery(request_id="req-2"))
    assert len(r.entries) == 1
    assert r.entries[0].event == "合成文本过短"


def test_query_logs_filter_keyword(log_file: Path) -> None:
    r = query_logs(LogQuery(keyword="过短"))
    assert len(r.entries) == 1


def test_query_logs_filter_keyword_in_fields(log_file: Path) -> None:
    """关键词应能命中业务字段值，不只 event。"""
    r = query_logs(LogQuery(keyword="elysia"))
    assert len(r.entries) >= 1


def test_query_logs_filter_logger(log_file: Path) -> None:
    r = query_logs(LogQuery(logger_name="routes.synthesis"))
    assert len(r.entries) == 2


def test_query_logs_only_errors(log_file: Path) -> None:
    r = query_logs(LogQuery(only_errors=True))
    assert len(r.entries) == 1
    assert r.entries[0].level == "ERROR"
    assert r.entries[0].exc


def test_query_logs_parses_fields(log_file: Path) -> None:
    r = query_logs(LogQuery(request_id="req-1"))
    e = r.entries[0]
    assert e.fields["voice_id"] == "elysia"
    assert e.fields["duration_s"] == 2.5


def test_query_logs_line_numbers(log_file: Path) -> None:
    r = query_logs(LogQuery())
    # 行号应从 1 开始且递增（倒序读取时递减）
    assert all(e.line_no >= 1 for e in r.entries)
    assert r.entries[0].line_no > r.entries[-1].line_no


def test_query_logs_tolerates_bad_lines(tmp_data_dir: Path) -> None:
    """坏行应跳过并计数，不能整体失败。"""
    from custom_voice.core.paths import log_dir

    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / "zjy14.log"
    p.write_text(
        json.dumps({"ts": "t1", "level": "INFO", "event": "好行1"}) + "\n"
        + "this is not json\n"
        + json.dumps({"ts": "t2", "level": "INFO", "event": "好行2"}) + "\n",
        encoding="utf-8",
    )
    r = query_logs(LogQuery())
    assert len(r.entries) == 2
    assert r.bad_lines >= 1


def test_query_logs_missing_file(tmp_data_dir: Path) -> None:
    r = query_logs(LogQuery())
    assert r.entries == []
    assert r.file_size == 0


def test_query_logs_redacts_secrets(tmp_data_dir: Path) -> None:
    """日志里的敏感字段必须脱敏（兜底）。"""
    from custom_voice.core.paths import log_dir

    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    p = d / "zjy14.log"
    p.write_text(
        json.dumps({
            "ts": "t", "level": "INFO", "event": "配置",
            "api_key": "sk-secret-value", "auth_token": "tok",
        }) + "\n",
        encoding="utf-8",
    )
    r = query_logs(LogQuery())
    e = r.entries[0]
    assert e.fields["api_key"] == "<set>"
    assert e.fields["auth_token"] == "<set>"
    assert "secret" not in json.dumps(e.to_dict())


def test_available_log_files(log_file: Path) -> None:
    files = available_log_files()
    assert len(files) >= 1
    cur = [f for f in files if f["is_current"]]
    assert len(cur) == 1
    assert cur[0]["name"] == "zjy14.log"
    assert cur[0]["size"] > 0


def test_available_log_files_empty(tmp_data_dir: Path) -> None:
    assert available_log_files() == []


# ----------------------------------------------------------------------
# 日志 HTTP 接口
# ----------------------------------------------------------------------


def test_http_logs(client, log_file: Path) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/logs")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 3
    assert len(d["entries"]) == 3


def test_http_logs_filter_level(client, log_file: Path) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/logs?level=ERROR")
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_http_logs_multiple_levels(client, log_file: Path) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/logs?level=WARNING&level=ERROR")
    assert r.status_code == 200
    assert r.json()["count"] == 2


def test_http_logs_request_id(client, log_file: Path) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/logs?request_id=req-1")
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_http_logs_only_errors(client, log_file: Path) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/logs?only_errors=true")
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_http_logs_limit_validation(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/logs?limit=0")
    assert r.status_code == 422


def test_http_logs_files(client, log_file: Path) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/logs/files")
    assert r.status_code == 200
    files = r.json()
    assert len(files) >= 1
    assert files[0]["name"] == "zjy14.log"
