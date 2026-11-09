"""系统信息端点：状态摘要与 GPU 信息。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

from ...config.loader import describe_config_sources
from ...core.logging import get_logger
from ...core.paths import describe_paths
from ..deps import get_state

router = APIRouter(prefix="/api/v1/system", tags=["system"])
logger = get_logger("routes.system")


@router.get("/status")
async def status(request: Request) -> dict:
    """服务状态摘要：版本、配置、路径、引擎、运行时长。"""
    state = get_state(request)
    cfg = state.config
    stats = state.engine.stats()

    return {
        "ok": True,
        "version": "0.1.0",
        "uptime_s": round(time.time() - state.started_at, 1),
        "engine": {
            "kind": stats.kind,
            "ready": stats.ready,
            "sample_rate": stats.sample_rate,
            "device": stats.device,
            "gpu": stats.gpu_name,
            "torch": stats.torch_version,
            "voices": stats.voices,
        },
        # 脱敏：auth_token 只报是否设置
        "config": {
            "engine": cfg.engine.value,
            "device": cfg.device,
            "precision": cfg.precision.value,
            "max_concurrent_requests": cfg.max_concurrent_requests,
            "request_timeout_s": cfg.request_timeout_s,
            "speaker_cache_enabled": cfg.speaker_cache_enabled,
            "speaker_cache_max_items": cfg.speaker_cache_max_items,
            "memory_safety_margin_mb": cfg.memory_safety_margin_mb,
            "token_hop_len_reset": cfg.token_hop_len_reset,
            "host": cfg.host,
            "port": cfg.port,
            "auth_token_set": bool(cfg.auth_token),
            "is_loopback": cfg.is_loopback(),
        },
        "config_sources": describe_config_sources(),
        "paths": describe_paths(),
        "db": {
            "tables": state.db.table_names(),
            "journal_mode": state.db.journal_mode(),
            "foreign_keys": state.db.foreign_keys_enabled(),
        },
    }


@router.get("/gpu")
async def gpu() -> dict:
    """GPU 与显存信息。

    探测逻辑在 inference/gpu_info.py，本层不直接依赖 torch。
    torch 不可用时返回明确说明，而不是抛异常。
    """
    from ...inference.gpu_info import gpu_info

    return gpu_info()
