"""健康检查端点。

关键约束：模型未就绪必须返回 503，**绝不假报 200 OK**。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...core.logging import get_logger
from ...schemas.synthesis import EngineStats
from ..deps import get_state

router = APIRouter(tags=["health"])
logger = get_logger("routes.health")


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """返回引擎健康状态。

    - 200：引擎就绪
    - 503：引擎未就绪（含应用尚未启动完成）
    """
    try:
        state = get_state(request)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "engine": "unknown",
                "reason": str(exc),
            },
        )

    stats: EngineStats = state.engine.stats()
    if not stats.ready:
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "engine": stats.kind,
                "reason": "engine not ready",
                "sample_rate": stats.sample_rate,
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "engine": stats.kind,
            "sample_rate": stats.sample_rate,
            "device": stats.device,
            "gpu": stats.gpu_name,
            "torch": stats.torch_version,
            "load_s": round(stats.load_s, 2),
            "warmup_s": round(stats.warmup_s, 2),
            "voices": [
                {"voice_id": v, "is_builtin": False} for v in stats.voices
            ],
            "peak_vram_mb": stats.peak_vram_mb,
        },
    )
