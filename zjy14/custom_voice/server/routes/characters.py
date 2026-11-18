"""角色管理 API。

端点：
    GET    /api/v1/characters
    POST   /api/v1/characters
    GET    /api/v1/characters/{character_id}
    PATCH  /api/v1/characters/{character_id}
    DELETE /api/v1/characters/{character_id}
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ...character.repository import Character, CharacterRepository
from ...core.errors import InvalidRequestError, NotFoundError
from ...core.logging import get_logger
from ...schemas.character import CharacterCreate, CharacterOut, CharacterUpdate
from ..deps import get_state

router = APIRouter(prefix="/api/v1/characters", tags=["characters"])
logger = get_logger("routes.characters")


def _repo(request: Request) -> CharacterRepository:
    return CharacterRepository(get_state(request).db)


@router.get("", response_model=list[CharacterOut])
async def list_characters(request: Request) -> list[CharacterOut]:
    """列出所有角色。"""
    chars = _repo(request).list_all()
    return [CharacterOut(**c.to_dict()) for c in chars]


@router.post("", response_model=CharacterOut, status_code=201)
async def create_character(request: Request, payload: CharacterCreate) -> CharacterOut:
    """创建角色。

    校验：
    - character_id 唯一
    - speaker_id 若指定，必须存在（不允许绑到不存在的声音）
    """
    state = get_state(request)
    repo = CharacterRepository(state.db)

    if repo.exists(payload.character_id):
        raise InvalidRequestError(
            f"角色 {payload.character_id!r} 已存在",
            detail={"character_id": payload.character_id},
        )

    if payload.speaker_id:
        from ...speaker.registry import SpeakerRepository

        if not SpeakerRepository(state.db).exists(payload.speaker_id):
            raise InvalidRequestError(
                f"声音档案 {payload.speaker_id!r} 不存在，请先创建声音。",
                detail={"speaker_id": payload.speaker_id},
            )

    ch = Character(**payload.model_dump())
    created = repo.create(ch)

    logger.info(
        "角色已创建",
        extra={
            "extra_fields": {
                "character_id": created.character_id,
                "speaker_id": created.speaker_id,
                "allowed_emotions": created.allowed_emotions,
            }
        },
    )
    return CharacterOut(**created.to_dict())


@router.get("/{character_id}", response_model=CharacterOut)
async def get_character(request: Request, character_id: str) -> CharacterOut:
    """取单个角色。"""
    ch = _repo(request).get(character_id)
    if ch is None:
        raise NotFoundError(
            f"角色 {character_id!r} 不存在", detail={"character_id": character_id}
        )
    return CharacterOut(**ch.to_dict())


@router.patch("/{character_id}", response_model=CharacterOut)
async def update_character(
    request: Request, character_id: str, payload: CharacterUpdate
) -> CharacterOut:
    """局部更新角色。只更新传入的字段。"""
    state = get_state(request)
    repo = CharacterRepository(state.db)

    if not repo.exists(character_id):
        raise NotFoundError(
            f"角色 {character_id!r} 不存在", detail={"character_id": character_id}
        )

    # 只取显式传入的字段（排除未设置的 None）
    fields = payload.model_dump(exclude_unset=True)

    if "speaker_id" in fields and fields["speaker_id"]:
        from ...speaker.registry import SpeakerRepository

        if not SpeakerRepository(state.db).exists(fields["speaker_id"]):
            raise InvalidRequestError(
                f"声音档案 {fields['speaker_id']!r} 不存在",
                detail={"speaker_id": fields["speaker_id"]},
            )

    updated = repo.update(character_id, fields)
    if updated is None:
        raise NotFoundError(f"角色 {character_id!r} 不存在")

    logger.info(
        "角色已更新",
        extra={"extra_fields": {"character_id": character_id, "fields": sorted(fields)}},
    )
    return CharacterOut(**updated.to_dict())


@router.delete("/{character_id}", status_code=204)
async def delete_character(request: Request, character_id: str) -> Response:
    """删除角色。

    注意：不级联删除声音档案（声音可能被多个角色共用）。
    """
    repo = _repo(request)
    if not repo.delete(character_id):
        raise NotFoundError(
            f"角色 {character_id!r} 不存在", detail={"character_id": character_id}
        )
    logger.info("角色已删除", extra={"extra_fields": {"character_id": character_id}})
    # 204 不允许有响应体，必须显式返回空 Response
    return Response(status_code=204)
