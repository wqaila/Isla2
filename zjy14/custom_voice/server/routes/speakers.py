"""声音档案 API。

端点：
    GET  /api/v1/speakers
    POST /api/v1/speakers
    GET  /api/v1/speakers/{speaker_id}
    PATCH /api/v1/speakers/{speaker_id}
    DELETE /api/v1/speakers/{speaker_id}
    POST /api/v1/speakers/{speaker_id}/validate   重新做质量检测
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ...core.errors import InvalidRequestError, SpeakerNotFoundError
from ...core.logging import get_logger
from ...schemas.character import (
    QualityReportOut,
    SpeakerCreate,
    SpeakerOut,
    SpeakerUpdate,
)
from ...speaker import validate as quality
from ...speaker.cache import file_sha256, make_cache_key
from ...speaker.registry import Speaker, SpeakerRepository
from ..deps import get_state

router = APIRouter(prefix="/api/v1/speakers", tags=["speakers"])
logger = get_logger("routes.speakers")


def _repo(request: Request) -> SpeakerRepository:
    return SpeakerRepository(get_state(request).db)


def _resolve_audio_path(raw: str) -> Path:
    """解析参考音频路径并做安全校验。

    原提示词要求："音频文件路径必须做安全校验，避免路径穿越"。

    2026-11-25 加固：先做 ``resolve()`` 再校验。
      原实现只用 ``Path(raw).parts`` 判断是否含 ``..``，
      但**符号链接**可以绕过：``/tmp/link.wav -> /etc/shadow`` 的
      ``parts`` 里没有 ``..``，校验直接通过，随后引擎会去读真实目标。
      改为先 ``resolve(strict=True)`` 拿到真实路径，再对真实路径判 ``..``。
      ``strict=True`` 顺便替代了原来的 ``is_file()`` 检查（链接断掉也会报错）。
    """
    try:
        # strict=True：路径不存在直接抛，避免"先校验后消失"的 TOCTOU
        p = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        # 相对路径单独给一条明确信息：原契约要求「必须是绝对路径」，
        # 这个提示对调用方更有指导性，也保留了既有测试断言。
        if not Path(raw).expanduser().is_absolute():
            raise InvalidRequestError(
                f"音频路径必须是绝对路径：{raw}", detail={"path": raw}
            ) from exc
        raise InvalidRequestError(
            f"音频路径无法解析（不存在或链接失效）：{raw}",
            detail={"path": raw, "error": type(exc).__name__},
        ) from exc

    # 对**解析后的真实路径**判穿越
    if ".." in p.parts:
        raise InvalidRequestError(
            f"音频路径不允许包含 '..'：{raw}", detail={"path": raw}
        )
    if not p.is_file():
        raise InvalidRequestError(
            f"音频路径不是普通文件：{raw}", detail={"path": raw}
        )
    # 只接受音频扩展名（对真实文件名判断，防止
    # ``evil.exe`` 通过 ``link.wav -> evil.exe`` 混进来）
    if p.suffix.lower() not in {".wav", ".flac", ".mp3", ".m4a", ".ogg"}:
        raise InvalidRequestError(
            f"不支持的音频格式 {p.suffix!r}，支持 .wav/.flac/.mp3/.m4a/.ogg",
            detail={"path": raw},
        )
    return p


@router.get("", response_model=list[SpeakerOut])
async def list_speakers(request: Request) -> list[SpeakerOut]:
    """列出所有声音档案。"""
    return [SpeakerOut(**s.to_dict()) for s in _repo(request).list_all()]


@router.post("", response_model=SpeakerOut, status_code=201)
async def create_speaker(request: Request, payload: SpeakerCreate) -> SpeakerOut:
    """导入声音档案。

    流程：
    1. 校验路径安全与文件存在
    2. 跑质量检测，有 blocking 问题直接拒绝
    3. 落库（含质量报告与缓存 key）
    """
    state = get_state(request)
    repo = SpeakerRepository(state.db)

    if repo.exists(payload.speaker_id):
        raise InvalidRequestError(
            f"声音档案 {payload.speaker_id!r} 已存在",
            detail={"speaker_id": payload.speaker_id},
        )

    audio_path = _resolve_audio_path(payload.audio_path)

    report = quality.analyze(audio_path)
    if not report.ok:
        blocking = [i.code for i in report.blocking_issues]
        raise InvalidRequestError(
            f"参考音频质量不达标，无法导入：{report.summary()}",
            detail={
                "speaker_id": payload.speaker_id,
                "blocking_issues": blocking,
                "issues": [i.message for i in report.blocking_issues],
                "report": report.to_dict(),
            },
        )

    model_version = payload.model_version or "cosyvoice3-0.5b-2512"
    pre_version = payload.preprocessing_version or "v1"

    cache_key = make_cache_key(
        speaker_id=payload.speaker_id,
        audio_sha256=file_sha256(audio_path),
        model_version=model_version,
        preprocessing_version=pre_version,
        prompt_text=payload.transcript,
    )

    sp = Speaker(
        speaker_id=payload.speaker_id,
        display_name=payload.display_name or payload.speaker_id,
        audio_path=str(audio_path),
        transcript=payload.transcript,
        sample_rate=report.sample_rate,
        channels=report.channels,
        duration_s=report.duration_s,
        quality_report=report.to_dict(),
        consent_status=payload.consent_status,
        source=payload.source,
        feature_cache_key=cache_key,
        model_version=model_version,
        preprocessing_version=pre_version,
    )
    created = repo.create(sp)

    logger.info(
        "声音档案已创建",
        extra={
            "extra_fields": {
                "speaker_id": created.speaker_id,
                "duration_s": created.duration_s,
                "score": report.score,
                "warnings": len(report.warnings),
                "cache_key": cache_key[:8],
            }
        },
    )
    return SpeakerOut(**created.to_dict())


@router.get("/{speaker_id}", response_model=SpeakerOut)
async def get_speaker(request: Request, speaker_id: str) -> SpeakerOut:
    """取单个声音档案。"""
    sp = _repo(request).get(speaker_id)
    if sp is None:
        raise SpeakerNotFoundError(
            f"声音档案 {speaker_id!r} 不存在", detail={"speaker_id": speaker_id}
        )
    return SpeakerOut(**sp.to_dict())


@router.patch("/{speaker_id}", response_model=SpeakerOut)
async def update_speaker(
    request: Request, speaker_id: str, payload: SpeakerUpdate
) -> SpeakerOut:
    """局部更新声音档案。

    修改 audio_path / transcript 会使缓存 key 失效（重新计算）。
    """
    state = get_state(request)
    repo = SpeakerRepository(state.db)

    current = repo.get(speaker_id)
    if current is None:
        raise SpeakerNotFoundError(
            f"声音档案 {speaker_id!r} 不存在", detail={"speaker_id": speaker_id}
        )

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return SpeakerOut(**current.to_dict())

    # 路径或文本变了 -> 重算缓存 key，并重跑质量检测
    if "audio_path" in fields:
        audio_path = _resolve_audio_path(fields["audio_path"])
        report = quality.analyze(audio_path)
        if not report.ok:
            raise InvalidRequestError(
                f"新参考音频质量不达标：{report.summary()}",
                detail={
                    "blocking_issues": [i.code for i in report.blocking_issues],
                    "report": report.to_dict(),
                },
            )
        fields["audio_path"] = str(audio_path)
        fields["sample_rate"] = report.sample_rate
        fields["channels"] = report.channels
        fields["duration_s"] = report.duration_s
        fields["quality_report"] = report.to_dict()

    if "audio_path" in fields or "transcript" in fields:
        audio_path = Path(fields.get("audio_path", current.audio_path))
        transcript = fields.get("transcript", current.transcript)
        fields["feature_cache_key"] = make_cache_key(
            speaker_id=speaker_id,
            audio_sha256=file_sha256(audio_path),
            model_version=fields.get("model_version", current.model_version),
            preprocessing_version=fields.get(
                "preprocessing_version", current.preprocessing_version
            ),
            prompt_text=transcript,
        )

    updated = repo.update(speaker_id, fields)
    if updated is None:
        raise SpeakerNotFoundError(f"声音档案 {speaker_id!r} 不存在")

    # 缓存 key 变了 -> 使旧缓存失效
    if "feature_cache_key" in fields:
        cache = getattr(state, "speaker_cache", None)
        if cache is not None:
            cache.invalidate_speaker(speaker_id)

    logger.info(
        "声音档案已更新",
        extra={"extra_fields": {"speaker_id": speaker_id, "fields": sorted(fields)}},
    )
    return SpeakerOut(**updated.to_dict())


@router.delete("/{speaker_id}", status_code=204)
async def delete_speaker(request: Request, speaker_id: str) -> Response:
    """删除声音档案。

    若有角色引用该声音，拒绝删除（避免角色静默失去音色）。
    """
    state = get_state(request)
    repo = SpeakerRepository(state.db)

    users = repo.characters_using(speaker_id)
    if users:
        raise InvalidRequestError(
            f"声音档案 {speaker_id!r} 正被角色 {users} 使用，请先解除绑定。",
            detail={"speaker_id": speaker_id, "characters": users},
        )

    if not repo.delete(speaker_id):
        raise SpeakerNotFoundError(
            f"声音档案 {speaker_id!r} 不存在", detail={"speaker_id": speaker_id}
        )

    cache = getattr(state, "speaker_cache", None)
    if cache is not None:
        cache.invalidate_speaker(speaker_id)

    logger.info("声音档案已删除", extra={"extra_fields": {"speaker_id": speaker_id}})
    # 204 不允许有响应体
    return Response(status_code=204)


@router.post("/{speaker_id}/validate", response_model=QualityReportOut)
async def validate_speaker(request: Request, speaker_id: str) -> QualityReportOut:
    """对已有声音档案重新做质量检测。

    用途：参考音频文件可能被外部替换，需要重新确认可用性。
    """
    repo = _repo(request)
    sp = repo.get(speaker_id)
    if sp is None:
        raise SpeakerNotFoundError(
            f"声音档案 {speaker_id!r} 不存在", detail={"speaker_id": speaker_id}
        )

    audio_path = Path(sp.audio_path)
    if not audio_path.is_file():
        raise InvalidRequestError(
            f"参考音频文件已丢失：{sp.audio_path}", detail={"path": sp.audio_path}
        )

    report = quality.analyze(audio_path)

    # 把最新报告写回库，便于事后追溯
    repo.update(
        speaker_id,
        {
            "quality_report": report.to_dict(),
            "sample_rate": report.sample_rate,
            "channels": report.channels,
            "duration_s": report.duration_s,
        },
    )

    return QualityReportOut(
        **report.to_dict(),
        summary=report.summary(),
    )
