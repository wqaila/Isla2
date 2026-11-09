"""声音档案仓储。

一个 speaker = 一份参考音频 + 逐字文本 + 元数据。
CosyVoice 零样本克隆强依赖"参考音频与文本严格对应"，
所以文本缺失或为空时必须拒绝，不能悄悄用空文本合成。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db.session import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Speaker:
    """声音档案。"""

    speaker_id: str
    display_name: str = ""
    audio_path: str = ""
    transcript: str = ""
    sample_rate: int = 0
    channels: int = 0
    duration_s: float = 0.0
    quality_report: dict[str, Any] = field(default_factory=dict)
    consent_status: str = "unknown"
    source: str = ""
    feature_cache_key: str = ""
    model_version: str = ""
    preprocessing_version: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker_id": self.speaker_id,
            "display_name": self.display_name,
            "audio_path": self.audio_path,
            "transcript": self.transcript,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "duration_s": self.duration_s,
            "quality_report": self.quality_report,
            "consent_status": self.consent_status,
            "source": self.source,
            "feature_cache_key": self.feature_cache_key,
            "model_version": self.model_version,
            "preprocessing_version": self.preprocessing_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Speaker":
        try:
            qr = json.loads(row["quality_report"] or "{}")
        except json.JSONDecodeError:
            qr = {}
        return cls(
            speaker_id=row["speaker_id"],
            display_name=row["display_name"],
            audio_path=row["audio_path"],
            transcript=row["transcript"],
            sample_rate=row["sample_rate"],
            channels=row["channels"],
            duration_s=row["duration_s"],
            quality_report=qr,
            consent_status=row["consent_status"],
            source=row["source"],
            feature_cache_key=row["feature_cache_key"],
            model_version=row["model_version"],
            preprocessing_version=row["preprocessing_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class SpeakerRepository:
    """声音档案 CRUD。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def list_all(self) -> list[Speaker]:
        conn = self._db.connect()
        rows = conn.execute(
            "SELECT * FROM speakers ORDER BY created_at ASC"
        ).fetchall()
        return [Speaker.from_row(r) for r in rows]

    def get(self, speaker_id: str) -> Speaker | None:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT * FROM speakers WHERE speaker_id = ?", (speaker_id,)
        ).fetchone()
        return Speaker.from_row(row) if row else None

    def exists(self, speaker_id: str) -> bool:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT 1 FROM speakers WHERE speaker_id = ?", (speaker_id,)
        ).fetchone()
        return row is not None

    def create(self, sp: Speaker) -> Speaker:
        now = _now()
        sp.created_at = now
        sp.updated_at = now
        with self._db.transaction() as tx:
            tx.execute(
                "INSERT INTO speakers ("
                "speaker_id, display_name, audio_path, transcript, sample_rate,"
                "channels, duration_s, quality_report, consent_status, source,"
                "feature_cache_key, model_version, preprocessing_version,"
                "created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sp.speaker_id,
                    sp.display_name,
                    sp.audio_path,
                    sp.transcript,
                    sp.sample_rate,
                    sp.channels,
                    sp.duration_s,
                    json.dumps(sp.quality_report, ensure_ascii=False),
                    sp.consent_status,
                    sp.source,
                    sp.feature_cache_key,
                    sp.model_version,
                    sp.preprocessing_version,
                    sp.created_at,
                    sp.updated_at,
                ),
            )
        return sp

    def update(self, speaker_id: str, fields: dict[str, Any]) -> Speaker | None:
        if self.get(speaker_id) is None:
            return None

        allowed_cols = {
            "display_name",
            "audio_path",
            "transcript",
            "sample_rate",
            "channels",
            "duration_s",
            "quality_report",
            "consent_status",
            "source",
            "feature_cache_key",
            "model_version",
            "preprocessing_version",
        }
        updates = {k: v for k, v in fields.items() if k in allowed_cols}
        if not updates:
            return self.get(speaker_id)

        if "quality_report" in updates and isinstance(updates["quality_report"], dict):
            updates["quality_report"] = json.dumps(
                updates["quality_report"], ensure_ascii=False
            )

        updates["updated_at"] = _now()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [speaker_id]

        with self._db.transaction() as tx:
            tx.execute(
                f"UPDATE speakers SET {assignments} WHERE speaker_id = ?", values
            )
        return self.get(speaker_id)

    def delete(self, speaker_id: str) -> bool:
        with self._db.transaction() as tx:
            cur = tx.execute(
                "DELETE FROM speakers WHERE speaker_id = ?", (speaker_id,)
            )
            return cur.rowcount > 0

    def characters_using(self, speaker_id: str) -> list[str]:
        """返回引用了该音色的角色 ID，删除前需检查。"""
        conn = self._db.connect()
        rows = conn.execute(
            "SELECT character_id FROM characters WHERE speaker_id = ?",
            (speaker_id,),
        ).fetchall()
        return [r["character_id"] for r in rows]


class ReferenceAudioRepository:
    """参考音频元数据。保留原始文件信息，不覆盖。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(
        self,
        *,
        audio_id: str,
        original_path: Path,
        stored_path: Path,
        sha256: str,
        sample_rate: int,
        channels: int,
        duration_s: float,
        speaker_id: str | None = None,
        source: str = "",
        license_note: str = "",
    ) -> None:
        with self._db.transaction() as tx:
            tx.execute(
                "INSERT INTO reference_audio ("
                "audio_id, speaker_id, original_path, stored_path, sha256,"
                "sample_rate, channels, duration_s, source, license_note, created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    audio_id,
                    speaker_id,
                    str(original_path),
                    str(stored_path),
                    sha256,
                    sample_rate,
                    channels,
                    duration_s,
                    source,
                    license_note,
                    _now(),
                ),
            )

    def list_for_speaker(self, speaker_id: str) -> list[dict[str, Any]]:
        conn = self._db.connect()
        rows = conn.execute(
            "SELECT * FROM reference_audio WHERE speaker_id = ? ORDER BY created_at",
            (speaker_id,),
        ).fetchall()
        return [dict(r) for r in rows]


__all__ = ["ReferenceAudioRepository", "Speaker", "SpeakerRepository"]
