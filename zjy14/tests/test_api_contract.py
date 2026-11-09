"""API 契约测试。

这是 Phase 1 最重要的测试：断言 zjy14 的 HTTP 接口与 zjy13 TTSClient 完全兼容。
依据：zjy13/server/tts/client.py 的实际消费方式。

zjy13 依赖的具体行为：
- POST /synthesize 返回 WAV 二进制
- 响应头 x-sample-rate / x-duration / x-total-ms / x-rtf / x-engine / x-voice-id
- 400 -> TTSBadRequestError
- 5xx -> TTSError
- GET /health 返回 JSON
"""

from __future__ import annotations

import io
import wave

import pytest

from custom_voice.schemas.synthesis import SYNTHESIS_RESPONSE_HEADERS


def _wav_info(data: bytes) -> tuple[int, int, int, float]:
    with wave.open(io.BytesIO(data), "rb") as w:
        return (
            w.getframerate(),
            w.getnchannels(),
            w.getsampwidth() * 8,
            w.getnframes() / w.getframerate(),
        )


# ----------------------------------------------------------------------
# /health
# ----------------------------------------------------------------------


def test_health_ok(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["engine"] == "stub"
    assert body["sample_rate"] == 24000
    assert isinstance(body["voices"], list)
    assert body["voices"][0]["voice_id"] == "elysia"


def test_health_returns_503_when_not_ready(app) -> None:  # type: ignore[no-untyped-def]
    """模型未就绪必须 503，绝不假报 200。"""
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        # lifespan 已 load，先手动关掉引擎模拟未就绪
        c.app.state.zjy14.engine.shutdown()
        r = c.get("/health")
        assert r.status_code == 503
        assert r.json()["ok"] is False


# ----------------------------------------------------------------------
# /synthesize 契约
# ----------------------------------------------------------------------


def test_synthesize_returns_wav(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/synthesize",
        json={"text": "你好", "voice_id": "elysia", "speed": 1.0},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/wav")

    sr, ch, bits, duration = _wav_info(r.content)
    assert sr == 24000
    assert ch == 1
    assert bits == 16
    assert duration == pytest.approx(0.4, abs=0.02)


def test_all_required_headers_present(client) -> None:  # type: ignore[no-untyped-def]
    """zjy13 依赖的 6 个响应头必须全部存在。"""
    r = client.post("/synthesize", json={"text": "你好"})
    assert r.status_code == 200
    for name in SYNTHESIS_RESPONSE_HEADERS:
        assert name in r.headers, f"缺少响应头 {name}"


def test_header_values_parseable(client) -> None:  # type: ignore[no-untyped-def]
    """响应头必须能被 zjy13 用 int()/float() 解析。"""
    r = client.post("/synthesize", json={"text": "你好"})
    assert int(r.headers["x-sample-rate"]) == 24000
    assert float(r.headers["x-duration"]) > 0
    assert float(r.headers["x-total-ms"]) > 0
    assert float(r.headers["x-rtf"]) > 0
    assert r.headers["x-engine"] == "stub"
    assert r.headers["x-voice-id"] == "elysia"


def test_request_id_echoed(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/synthesize", json={"text": "你好"})
    assert "x-request-id" in r.headers
    assert len(r.headers["x-request-id"]) > 0


def test_request_id_passthrough(client) -> None:  # type: ignore[no-untyped-def]
    """调用方传入的 request_id 应原样回传，便于跨服务对账。"""
    r = client.post(
        "/synthesize",
        json={"text": "你好"},
        headers={"x-request-id": "my-trace-001"},
    )
    assert r.headers["x-request-id"] == "my-trace-001"


# ----------------------------------------------------------------------
# 错误语义（zjy13 按状态码分支）
# ----------------------------------------------------------------------


def test_unknown_voice_returns_400(client) -> None:  # type: ignore[no-untyped-def]
    """zjy13 用 400 判定 TTSBadRequestError。"""
    r = client.post("/synthesize", json={"text": "你好", "voice_id": "nobody"})
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "invalid_request"


def test_empty_text_rejected(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/synthesize", json={"text": "   "})
    assert r.status_code == 422


def test_missing_text_rejected(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/synthesize", json={"voice_id": "elysia"})
    assert r.status_code == 422


def test_extra_field_rejected(client) -> None:  # type: ignore[no-untyped-def]
    """请求体多余字段必须报错，防止调用方误以为参数生效。"""
    r = client.post("/synthesize", json={"text": "你好", "unknown_knob": 1})
    assert r.status_code == 422


def test_speed_out_of_range(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/synthesize", json={"text": "你好", "speed": 5.0})
    assert r.status_code == 422


def test_synthesize_503_when_not_ready(app) -> None:  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        c.app.state.zjy14.engine.shutdown()
        r = c.post("/synthesize", json={"text": "你好"})
        assert r.status_code == 503
        assert r.json()["code"] == "engine_not_ready"


# ----------------------------------------------------------------------
# Phase 2 扩展字段：Phase 1 接受但不生效
# ----------------------------------------------------------------------


def test_emotion_fields_accepted(client) -> None:  # type: ignore[no-untyped-def]
    """情绪字段现在就能传（Phase 2 生效），保证接口向后兼容。"""
    r = client.post(
        "/synthesize",
        json={
            "text": "你好",
            "emotion": "happy",
            "intensity": 0.8,
            "style": "gentle",
            "instruction": "温柔一点",
        },
    )
    assert r.status_code == 200


def test_v1_alias_works(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post("/api/v1/synthesis", json={"text": "你好"})
    assert r.status_code == 200
    assert "x-engine" in r.headers


# ----------------------------------------------------------------------
# 系统端点
# ----------------------------------------------------------------------


def test_system_status(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/system/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["engine"]["kind"] == "stub"
    # 脱敏：不得出现 auth_token 明文
    assert "auth_token" not in body["config"]
    assert "auth_token_set" in body["config"]


def test_system_status_lists_db_tables(client) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/system/status").json()
    assert "characters" in body["db"]["tables"]
    assert body["db"]["foreign_keys"] is True


def test_system_gpu_does_not_crash(client) -> None:  # type: ignore[no-untyped-def]
    """torch 不可用时应返回说明，而不是 500。"""
    r = client.get("/api/v1/system/gpu")
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body
    if body["ok"]:
        assert body["total_mb"] > 0


def test_synthesis_recorded_in_db(client, tmp_data_dir) -> None:  # type: ignore[no-untyped-def]
    """每次合成应落库，便于事后分析。"""
    client.post("/synthesize", json={"text": "测试落库", "voice_id": "elysia"})

    from custom_voice.db.session import Database

    db = Database(tmp_data_dir / "custom_voice.db")
    try:
        conn = db.connect()
        rows = conn.execute("SELECT * FROM synthesis_logs").fetchall()
        assert len(rows) >= 1
        assert rows[-1]["engine"] == "stub"
        assert rows[-1]["text"] == "测试落库"
    finally:
        db.close()
