"""角色档案仓储。

职责边界：
- 只管数据存取，不含业务规则（业务在 routes 或 service 层）
- 所有时间戳由本层统一生成，避免调用方各自实现
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..db.session import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class Character:
    """角色档案。"""

    character_id: str
    display_name: str
    description: str = ""
    personality: str = ""
    speaking_habits: str = ""
    default_emotion: str = "neutral"
    default_style: str = ""
    speaker_id: str | None = None
    language: str = "zh"
    allowed_emotions: list[str] = field(default_factory=list)
    voice_instruction_template: str = ""
    model_id: str = ""
    model_version: str = ""
    config_version: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "display_name": self.display_name,
            "description": self.description,
            "personality": self.personality,
            "speaking_habits": self.speaking_habits,
            "default_emotion": self.default_emotion,
            "default_style": self.default_style,
            "speaker_id": self.speaker_id,
            "language": self.language,
            "allowed_emotions": self.allowed_emotions,
            "voice_instruction_template": self.voice_instruction_template,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "config_version": self.config_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Character":
        allowed = row["allowed_emotions"] or ""
        return cls(
            character_id=row["character_id"],
            display_name=row["display_name"],
            description=row["description"],
            personality=row["personality"],
            speaking_habits=row["speaking_habits"],
            default_emotion=row["default_emotion"],
            default_style=row["default_style"],
            speaker_id=row["speaker_id"],
            language=row["language"],
            allowed_emotions=[e.strip() for e in allowed.split(",") if e.strip()],
            voice_instruction_template=row["voice_instruction_template"],
            model_id=row["model_id"],
            model_version=row["model_version"],
            config_version=row["config_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class CharacterRepository:
    """角色 CRUD。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------

    def list_all(self) -> list[Character]:
        conn = self._db.connect()
        rows = conn.execute(
            "SELECT * FROM characters ORDER BY created_at ASC"
        ).fetchall()
        return [Character.from_row(r) for r in rows]

    def get(self, character_id: str) -> Character | None:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT * FROM characters WHERE character_id = ?", (character_id,)
        ).fetchone()
        return Character.from_row(row) if row else None

    def exists(self, character_id: str) -> bool:
        conn = self._db.connect()
        row = conn.execute(
            "SELECT 1 FROM characters WHERE character_id = ?", (character_id,)
        ).fetchone()
        return row is not None

    def create(self, ch: Character) -> Character:
        now = _now()
        ch.created_at = now
        ch.updated_at = now
        with self._db.transaction() as tx:
            tx.execute(
                "INSERT INTO characters ("
                "character_id, display_name, description, personality,"
                "speaking_habits, default_emotion, default_style, speaker_id,"
                "language, allowed_emotions, voice_instruction_template,"
                "model_id, model_version, config_version, created_at, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ch.character_id,
                    ch.display_name,
                    ch.description,
                    ch.personality,
                    ch.speaking_habits,
                    ch.default_emotion,
                    ch.default_style,
                    ch.speaker_id,
                    ch.language,
                    ",".join(ch.allowed_emotions),
                    ch.voice_instruction_template,
                    ch.model_id,
                    ch.model_version,
                    ch.config_version,
                    ch.created_at,
                    ch.updated_at,
                ),
            )
        return ch

    def update(self, character_id: str, fields: dict[str, Any]) -> Character | None:
        """局部更新。只更新传入的字段。"""
        current = self.get(character_id)
        if current is None:
            return None

        # allowed_emotions 是 list，落库要转字符串
        if "allowed_emotions" in fields and isinstance(fields["allowed_emotions"], list):
            fields = {**fields, "allowed_emotions": ",".join(fields["allowed_emotions"])}

        allowed_cols = {
            "display_name",
            "description",
            "personality",
            "speaking_habits",
            "default_emotion",
            "default_style",
            "speaker_id",
            "language",
            "allowed_emotions",
            "voice_instruction_template",
            "model_id",
            "model_version",
            "config_version",
        }
        updates = {k: v for k, v in fields.items() if k in allowed_cols}
        if not updates:
            return current

        updates["updated_at"] = _now()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [character_id]

        with self._db.transaction() as tx:
            tx.execute(
                f"UPDATE characters SET {assignments} WHERE character_id = ?",
                values,
            )
        return self.get(character_id)

    def delete(self, character_id: str) -> bool:
        with self._db.transaction() as tx:
            cur = tx.execute(
                "DELETE FROM characters WHERE character_id = ?", (character_id,)
            )
            return cur.rowcount > 0


__all__ = ["Character", "CharacterRepository"]
