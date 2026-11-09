"""合成端点。

契约硬约束（D2）：必须与 zjy13 的 TTSClient 兼容。
- 请求：``{"text", "voice_id", "speed"}``
- 响应：WAV 二进制 + 6 个响应头
- 状态码：400 参数错误 / 503 未就绪 / 5xx 内部错误
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ...core.errors import InvalidRequestError
from ...core.logging import get_logger, get_request_id
from ...schemas.synthesis import SynthesisRequest
from ..deps import get_state

router = APIRouter(tags=["synthesis"])
logger = get_logger("routes.synthesis")


def _record_synthesis(request: Request, req: SynthesisRequest, result) -> None:  # type: ignore[no-untyped-def]
    """把合成记录写入 SQLite。失败不影响主流程。"""
    try:
        state = get_state(request)
        with state.db.transaction() as tx:
            tx.execute(
                "INSERT INTO synthesis_logs ("
                "request_id, text, emotion, style, speed, instruction,"
                "sample_rate, duration_s, total_ms, first_audio_ms, rtf,"
                "peak_vram_mb, engine, created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    get_request_id() or result.request_id,
                    req.text,
                    req.emotion or "",
                    req.style or "",
                    req.speed,
                    req.instruction or "",
                    result.sample_rate,
                    result.duration_s,
                    result.total_ms,
                    result.first_audio_ms,
                    result.rtf,
                    result.peak_vram_mb,
                    result.engine,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
    except Exception:  # noqa: BLE001
        logger.exception("写入合成记录失败（不影响响应）")


@router.post("/synthesize")
async def synthesize(request: Request, payload: SynthesisRequest) -> Response:
    """合成语音，返回 WAV。

    请求体校验失败由 FastAPI 返回 422；这里额外把业务级参数错误转成 400，
    因为 zjy13 的 TTSClient 用 400 判定 ``TTSBadRequestError``。
    """
    state = get_state(request)

    if not state.engine.is_ready():
        from ...core.errors import EngineNotReadyError

        raise EngineNotReadyError("引擎尚未就绪，请稍后重试")

    started = time.perf_counter()
    result = state.engine.synthesize(payload)
    result.request_id = get_request_id()
    elapsed_ms = (time.perf_counter() - started) * 1000

    _record_synthesis(request, payload, result)

    logger.info(
        "合成完成",
        extra={
            "extra_fields": {
                "voice_id": payload.voice_id,
                "chars": len(payload.text),
                "duration_s": round(result.duration_s, 3),
                "total_ms": round(elapsed_ms, 1),
                "rtf": round(result.rtf, 3),
                "engine": result.engine,
            }
        },
    )

    return Response(
        content=result.wav_bytes,
        media_type="audio/wav",
        headers=result.response_headers(),
    )


@router.post("/api/v1/synthesis")
async def synthesize_v1(request: Request, payload: SynthesisRequest) -> Response:
    """带版本前缀的同一接口，便于后续演进。"""
    return await synthesize(request, payload)
