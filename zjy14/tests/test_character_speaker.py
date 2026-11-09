"""Phase 3 测试：仓储层与缓存。"""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_voice.character.repository import Character, CharacterRepository
from custom_voice.db.migrate import migrate
from custom_voice.db.session import Database
from custom_voice.speaker.cache import (
    SpeakerConditionCache,
    file_sha256,
    make_cache_key,
)
from custom_voice.speaker.registry import Speaker, SpeakerRepository


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "t.db")
    migrate(d)
    yield d
    d.close()


# ----------------------------------------------------------------------
# 角色仓储
# ----------------------------------------------------------------------


def test_character_crud(db: Database) -> None:
    repo = CharacterRepository(db)
    ch = repo.create(
        Character(
            character_id="c1",
            display_name="测试角色",
            allowed_emotions=["neutral", "happy"],
        )
    )
    assert ch.created_at

    got = repo.get("c1")
    assert got is not None
    assert got.display_name == "测试角色"
    assert got.allowed_emotions == ["neutral", "happy"]

    updated = repo.update("c1", {"display_name": "改名后"})
    assert updated is not None
    assert updated.display_name == "改名后"
    assert updated.updated_at >= got.updated_at

    assert repo.delete("c1") is True
    assert repo.get("c1") is None


def test_character_list(db: Database) -> None:
    repo = CharacterRepository(db)
    for i in range(3):
        repo.create(Character(character_id=f"c{i}", display_name=f"角色{i}"))
    assert len(repo.list_all()) == 3


def test_character_update_ignores_unknown_fields(db: Database) -> None:
    """不在白名单的字段必须被忽略，不能拼进 SQL。"""
    repo = CharacterRepository(db)
    repo.create(Character(character_id="c1", display_name="a"))
    out = repo.update("c1", {"display_name": "b", "evil_field": "x"})
    assert out is not None
    assert out.display_name == "b"


def test_character_delete_missing(db: Database) -> None:
    assert CharacterRepository(db).delete("nope") is False


def test_character_allowed_emotions_roundtrip(db: Database) -> None:
    repo = CharacterRepository(db)
    emotions = ["neutral", "happy", "sad", "angry"]
    repo.create(
        Character(character_id="c1", display_name="x", allowed_emotions=emotions)
    )
    got = repo.get("c1")
    assert got is not None
    assert got.allowed_emotions == emotions


# ----------------------------------------------------------------------
# 声音仓储
# ----------------------------------------------------------------------


def test_speaker_crud(db: Database) -> None:
    repo = SpeakerRepository(db)
    repo.create(
        Speaker(
            speaker_id="s1",
            audio_path="/tmp/a.wav",
            transcript="你好",
            sample_rate=24000,
            duration_s=8.0,
        )
    )
    got = repo.get("s1")
    assert got is not None
    assert got.transcript == "你好"
    assert repo.exists("s1")

    repo.update("s1", {"transcript": "改过的文本"})
    assert repo.get("s1").transcript == "改过的文本"  # type: ignore[union-attr]
    assert repo.delete("s1") is True


def test_speaker_quality_report_json_roundtrip(db: Database) -> None:
    repo = SpeakerRepository(db)
    report = {"ok": True, "score": 0.85, "issues": []}
    repo.create(
        Speaker(
            speaker_id="s1",
            audio_path="/tmp/a.wav",
            transcript="t",
            quality_report=report,
        )
    )
    got = repo.get("s1")
    assert got is not None
    assert got.quality_report == report


def test_speaker_characters_using(db: Database) -> None:
    sp = SpeakerRepository(db)
    sp.create(Speaker(speaker_id="s1", audio_path="/a.wav", transcript="t"))

    ch = CharacterRepository(db)
    ch.create(Character(character_id="c1", display_name="a", speaker_id="s1"))

    assert sp.characters_using("s1") == ["c1"]
    assert sp.characters_using("s2") == []


def test_speaker_delete_cascades_reference_audio(db: Database) -> None:
    """删除 speaker 时 reference_audio 应级联删除。"""
    sp = SpeakerRepository(db)
    sp.create(Speaker(speaker_id="s1", audio_path="/a.wav", transcript="t"))

    from custom_voice.speaker.registry import ReferenceAudioRepository

    ra = ReferenceAudioRepository(db)
    ra.create(
        audio_id="a1",
        original_path=Path("/orig.wav"),
        stored_path=Path("/stored.wav"),
        sha256="abc",
        sample_rate=24000,
        channels=1,
        duration_s=8.0,
        speaker_id="s1",
    )
    assert len(ra.list_for_speaker("s1")) == 1

    sp.delete("s1")
    assert ra.list_for_speaker("s1") == []


# ----------------------------------------------------------------------
# 缓存
# ----------------------------------------------------------------------


def test_cache_key_changes_with_audio(tmp_path: Path) -> None:
    p1 = tmp_path / "a.wav"
    p2 = tmp_path / "b.wav"
    p1.write_bytes(b"content-a")
    p2.write_bytes(b"content-b")

    k1 = make_cache_key(
        speaker_id="s1",
        audio_sha256=file_sha256(p1),
        model_version="v1",
        preprocessing_version="p1",
    )
    k2 = make_cache_key(
        speaker_id="s1",
        audio_sha256=file_sha256(p2),
        model_version="v1",
        preprocessing_version="p1",
    )
    assert k1 != k2


def test_cache_key_changes_with_model_version() -> None:
    k1 = make_cache_key(
        speaker_id="s1", audio_sha256="abc", model_version="v1",
        preprocessing_version="p1",
    )
    k2 = make_cache_key(
        speaker_id="s1", audio_sha256="abc", model_version="v2",
        preprocessing_version="p1",
    )
    assert k1 != k2


def test_cache_key_changes_with_transcript() -> None:
    """参考文本变化也必须使缓存失效（文本与音频错位会导致合成跑飞）。"""
    k1 = make_cache_key(
        speaker_id="s1", audio_sha256="abc", model_version="v1",
        preprocessing_version="p1", prompt_text="文本A",
    )
    k2 = make_cache_key(
        speaker_id="s1", audio_sha256="abc", model_version="v1",
        preprocessing_version="p1", prompt_text="文本B",
    )
    assert k1 != k2


def test_cache_key_stable() -> None:
    """同样的输入必须得到同样的 key。"""
    args = dict(
        speaker_id="s1", audio_sha256="abc", model_version="v1",
        preprocessing_version="p1", prompt_text="t",
    )
    assert make_cache_key(**args) == make_cache_key(**args)


def test_cache_lru_eviction() -> None:
    c = SpeakerConditionCache[int](max_items=2)
    c.put("k1", "s1", 1)
    c.put("k2", "s1", 2)
    c.put("k3", "s1", 3)  # 触发淘汰 k1

    assert c.get("k1") is None
    assert c.get("k2") == 2
    assert c.get("k3") == 3
    assert c.stats()["evictions"] == 1


def test_cache_hit_moves_to_end() -> None:
    c = SpeakerConditionCache[int](max_items=2)
    c.put("k1", "s1", 1)
    c.put("k2", "s1", 2)
    c.get("k1")  # k1 变为最近使用
    c.put("k3", "s1", 3)  # 应淘汰 k2

    assert c.get("k1") == 1
    assert c.get("k2") is None


def test_cache_invalidate_speaker() -> None:
    c = SpeakerConditionCache[int](max_items=8)
    c.put("k1", "s1", 1)
    c.put("k2", "s1", 2)
    c.put("k3", "s2", 3)

    removed = c.invalidate_speaker("s1")
    assert removed == 2
    assert c.get("k1") is None
    assert c.get("k3") == 3


def test_cache_disabled_never_stores() -> None:
    c = SpeakerConditionCache[int](max_items=8, enabled=False)
    c.put("k1", "s1", 1)
    assert c.get("k1") is None
    assert c.stats()["size"] == 0


def test_cache_stats_hit_rate() -> None:
    c = SpeakerConditionCache[int](max_items=8)
    c.put("k1", "s1", 1)
    c.get("k1")  # hit
    c.get("nope")  # miss
    s = c.stats()
    assert s["hits"] == 1
    assert s["misses"] == 1
    assert s["hit_rate"] == 0.5


def test_cache_rejects_zero_capacity() -> None:
    with pytest.raises(ValueError):
        SpeakerConditionCache(max_items=0)
