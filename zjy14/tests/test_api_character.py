"""Phase 3 API 测试：角色与声音管理端点。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf


@pytest.fixture()
def good_wav(tmp_path: Path) -> Path:
    """一段质量合格的参考音频（8 秒、24kHz、单声道、干净正弦）。"""
    sr = 24000
    t = np.arange(int(8.0 * sr)) / sr
    data = (np.sin(2 * np.pi * 220 * t) * 0.5).astype(np.float32)
    p = tmp_path / "ref_good.wav"
    sf.write(str(p), data, sr, subtype="PCM_16")
    return p


@pytest.fixture()
def bad_wav(tmp_path: Path) -> Path:
    """质量不合格：只有 1 秒。"""
    sr = 24000
    t = np.arange(int(1.0 * sr)) / sr
    data = (np.sin(2 * np.pi * 220 * t) * 0.5).astype(np.float32)
    p = tmp_path / "ref_bad.wav"
    sf.write(str(p), data, sr, subtype="PCM_16")
    return p


def _make_speaker(client, path: Path, speaker_id: str = "s1"):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/speakers",
        json={
            "speaker_id": speaker_id,
            "display_name": "测试音色",
            "audio_path": str(path),
            "transcript": "这是一段参考音频的文本。",
        },
    )


# ----------------------------------------------------------------------
# 声音档案
# ----------------------------------------------------------------------


def test_create_speaker_ok(client, good_wav: Path) -> None:  # type: ignore[no-untyped-def]
    r = _make_speaker(client, good_wav)
    assert r.status_code == 201
    body = r.json()
    assert body["speaker_id"] == "s1"
    assert body["sample_rate"] == 24000
    assert body["channels"] == 1
    assert body["duration_s"] == pytest.approx(8.0, abs=0.05)
    assert body["feature_cache_key"]  # 必须生成缓存 key
    assert body["quality_report"]["ok"] is True


def test_create_speaker_rejects_low_quality(client, bad_wav: Path) -> None:  # type: ignore[no-untyped-def]
    """质量不达标必须拒绝导入，并说明原因。"""
    r = _make_speaker(client, bad_wav)
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "invalid_request"
    assert "too_short" in body["detail"]["blocking_issues"]


def test_create_speaker_missing_file(client, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/v1/speakers",
        json={
            "speaker_id": "s1",
            "audio_path": str(tmp_path / "nope.wav"),
            "transcript": "文本",
        },
    )
    assert r.status_code == 400
    assert "不存在" in r.json()["message"]


def test_create_speaker_rejects_path_traversal(client) -> None:  # type: ignore[no-untyped-def]
    """路径穿越必须拒绝（原提示词安全要求）。"""
    r = client.post(
        "/api/v1/speakers",
        json={
            "speaker_id": "s1",
            "audio_path": "/tmp/../etc/passwd.wav",
            "transcript": "文本",
        },
    )
    assert r.status_code == 400
    assert ".." in r.json()["message"]


def test_create_speaker_requires_absolute_path(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/v1/speakers",
        json={
            "speaker_id": "s1",
            "audio_path": "relative/path.wav",
            "transcript": "文本",
        },
    )
    assert r.status_code == 400
    assert "绝对路径" in r.json()["message"]


def test_create_speaker_rejects_empty_transcript(client, good_wav: Path) -> None:  # type: ignore[no-untyped-def]
    """空文本必须拒绝 —— 零样本克隆没文本会严重跑偏。"""
    r = client.post(
        "/api/v1/speakers",
        json={
            "speaker_id": "s1",
            "audio_path": str(good_wav),
            "transcript": "   ",
        },
    )
    assert r.status_code == 422


def test_create_speaker_duplicate(client, good_wav: Path) -> None:  # type: ignore[no-untyped-def]
    assert _make_speaker(client, good_wav).status_code == 201
    r = _make_speaker(client, good_wav)
    assert r.status_code == 400
    assert "已存在" in r.json()["message"]


def test_list_and_get_speaker(client, good_wav: Path) -> None:  # type: ignore[no-untyped-def]
    _make_speaker(client, good_wav)
    lst = client.get("/api/v1/speakers")
    assert lst.status_code == 200
    assert len(lst.json()) == 1

    got = client.get("/api/v1/speakers/s1")
    assert got.status_code == 200
    assert got.json()["speaker_id"] == "s1"


def test_get_missing_speaker(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/speakers/nobody")
    assert r.status_code == 404
    assert r.json()["code"] == "speaker_not_found"


def test_validate_speaker_endpoint(client, good_wav: Path) -> None:  # type: ignore[no-untyped-def]
    _make_speaker(client, good_wav)
    r = client.post("/api/v1/speakers/s1/validate")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["summary"].startswith("可用")


def test_update_transcript_changes_cache_key(client, good_wav: Path) -> None:  # type: ignore[no-untyped-def]
    """改参考文本必须让缓存 key 变化（否则缓存会给出错误的音色条件）。"""
    _make_speaker(client, good_wav)
    before = client.get("/api/v1/speakers/s1").json()["feature_cache_key"]

    r = client.patch("/api/v1/speakers/s1", json={"transcript": "完全不同的文本"})
    assert r.status_code == 200
    after = r.json()["feature_cache_key"]
    assert before != after


def test_update_speaker_display_name_keeps_key(client, good_wav: Path) -> None:  # type: ignore[no-untyped-def]
    """只改显示名不应让缓存失效。"""
    _make_speaker(client, good_wav)
    before = client.get("/api/v1/speakers/s1").json()["feature_cache_key"]
    r = client.patch("/api/v1/speakers/s1", json={"display_name": "新名字"})
    assert r.json()["feature_cache_key"] == before


def test_delete_speaker_blocked_when_used(client, good_wav: Path) -> None:  # type: ignore[no-untyped-def]
    """被角色引用的声音不允许删除。"""
    _make_speaker(client, good_wav)
    client.post(
        "/api/v1/characters",
        json={"character_id": "c1", "display_name": "角色", "speaker_id": "s1"},
    )
    r = client.delete("/api/v1/speakers/s1")
    assert r.status_code == 400
    assert "正被角色" in r.json()["message"]


def test_delete_speaker_ok(client, good_wav: Path) -> None:  # type: ignore[no-untyped-def]
    _make_speaker(client, good_wav)
    r = client.delete("/api/v1/speakers/s1")
    assert r.status_code == 204
    assert client.get("/api/v1/speakers/s1").status_code == 404


# ----------------------------------------------------------------------
# 角色
# ----------------------------------------------------------------------


def test_create_character_ok(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/v1/characters",
        json={
            "character_id": "c1",
            "display_name": "小满",
            "default_emotion": "neutral",
            "allowed_emotions": ["neutral", "happy"],
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["character_id"] == "c1"
    assert body["allowed_emotions"] == ["neutral", "happy"]
    assert body["created_at"]


def test_create_character_invalid_emotion(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/v1/characters",
        json={
            "character_id": "c1",
            "display_name": "x",
            "allowed_emotions": ["ecstatic"],
        },
    )
    assert r.status_code == 422


def test_create_character_invalid_id(client) -> None:  # type: ignore[no-untyped-def]
    """含路径分隔符的 ID 必须拒绝（要能安全放进 URL 和文件名）。"""
    r = client.post(
        "/api/v1/characters",
        json={"character_id": "a/b", "display_name": "x"},
    )
    assert r.status_code == 422


def test_create_character_binds_missing_speaker(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/v1/characters",
        json={"character_id": "c1", "display_name": "x", "speaker_id": "ghost"},
    )
    assert r.status_code == 400
    assert "不存在" in r.json()["message"]


def test_create_character_binds_existing_speaker(client, good_wav: Path) -> None:  # type: ignore[no-untyped-def]
    _make_speaker(client, good_wav)
    r = client.post(
        "/api/v1/characters",
        json={"character_id": "c1", "display_name": "x", "speaker_id": "s1"},
    )
    assert r.status_code == 201
    assert r.json()["speaker_id"] == "s1"


def test_character_duplicate(client) -> None:  # type: ignore[no-untyped-def]
    body = {"character_id": "c1", "display_name": "x"}
    assert client.post("/api/v1/characters", json=body).status_code == 201
    r = client.post("/api/v1/characters", json=body)
    assert r.status_code == 400


def test_list_characters(client) -> None:  # type: ignore[no-untyped-def]
    for i in range(3):
        client.post(
            "/api/v1/characters",
            json={"character_id": f"c{i}", "display_name": f"角色{i}"},
        )
    r = client.get("/api/v1/characters")
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_get_missing_character(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/api/v1/characters/nope")
    assert r.status_code == 404


def test_patch_character(client) -> None:  # type: ignore[no-untyped-def]
    client.post(
        "/api/v1/characters", json={"character_id": "c1", "display_name": "旧名"}
    )
    r = client.patch("/api/v1/characters/c1", json={"display_name": "新名"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "新名"


def test_patch_character_partial_keeps_other_fields(client) -> None:  # type: ignore[no-untyped-def]
    """局部更新不能把未传字段清空。"""
    client.post(
        "/api/v1/characters",
        json={
            "character_id": "c1",
            "display_name": "名",
            "personality": "活泼",
            "default_style": "gentle",
        },
    )
    r = client.patch("/api/v1/characters/c1", json={"display_name": "改名"})
    body = r.json()
    assert body["personality"] == "活泼"
    assert body["default_style"] == "gentle"


def test_patch_missing_character(client) -> None:  # type: ignore[no-untyped-def]
    r = client.patch("/api/v1/characters/nope", json={"display_name": "x"})
    assert r.status_code == 404


def test_delete_character(client) -> None:  # type: ignore[no-untyped-def]
    client.post(
        "/api/v1/characters", json={"character_id": "c1", "display_name": "x"}
    )
    assert client.delete("/api/v1/characters/c1").status_code == 204
    assert client.get("/api/v1/characters/c1").status_code == 404


def test_delete_missing_character(client) -> None:  # type: ignore[no-untyped-def]
    assert client.delete("/api/v1/characters/nope").status_code == 404


def test_character_extra_field_rejected(client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/api/v1/characters",
        json={"character_id": "c1", "display_name": "x", "bogus": 1},
    )
    assert r.status_code == 422
