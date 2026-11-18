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


def _scheduler_stats(state) -> dict:  # type: ignore[no-untyped-def]
    """读取调度器统计。调度器未启用时返回明确说明。"""
    sched = getattr(state, "scheduler", None)
    if sched is None:
        return {"enabled": False, "reason": "调度器未初始化"}
    try:
        return {"enabled": True, **sched.stats()}
    except Exception:  # noqa: BLE001
        logger.exception("读取调度器统计失败")
        return {"enabled": False, "reason": "统计读取失败"}


def _bridge_compat() -> dict:
    """返回与 zjy13 设备协议的兼容信息。"""
    from ...bridge import describe_compat

    try:
        return describe_compat()
    except Exception:  # noqa: BLE001
        logger.exception("读取桥接兼容信息失败")
        return {"error": "读取失败"}


def _cleanup_stats(state) -> dict:  # type: ignore[no-untyped-def]
    """读取自动清理状态。未启用时返回明确说明。"""
    sched = getattr(state, "cleanup", None)
    if sched is None:
        return {"enabled": False, "reason": "清理调度器未初始化"}
    try:
        return sched.stats()
    except Exception:  # noqa: BLE001
        logger.exception("读取清理状态失败")
        return {"enabled": False, "reason": "读取失败"}


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
            "max_queue_size": cfg.max_queue_size,
            "queue_timeout_s": cfg.queue_timeout_s,
            "request_timeout_s": cfg.request_timeout_s,
            "stream_enabled": cfg.stream_enabled,
            "stream_char_threshold": cfg.stream_char_threshold,
            "speaker_cache_enabled": cfg.speaker_cache_enabled,
            "speaker_cache_max_items": cfg.speaker_cache_max_items,
            "memory_safety_margin_mb": cfg.memory_safety_margin_mb,
            "token_hop_len_reset": cfg.token_hop_len_reset,
            "text_frontend": cfg.text_frontend.value,
            "default_voice_id": cfg.default_voice_id,
            "min_text_ratio": cfg.min_text_ratio,
            "model_dir": cfg.model_dir or "(默认)",
            "host": cfg.host,
            "port": cfg.port,
            "auth_token_set": bool(cfg.auth_token),
            "is_loopback": cfg.is_loopback(),
        },
        # 调度器实时统计（并发/队列/拒绝/超时）
        "scheduler": _scheduler_stats(state),
        # 自动清理状态（保留期、上次运行、线程存活）
        "cleanup": _cleanup_stats(state),
        # 与 zjy13 设备协议的兼容对照（证明不重复造轮子）
        "bridge": _bridge_compat(),
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
