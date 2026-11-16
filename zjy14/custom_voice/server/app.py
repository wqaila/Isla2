"""FastAPI 应用组装与生命周期。

分层铁律：本文件及 routes/ 下**不允许 import cosyvoice**。
引擎通过 ``inference.engine.TTSEngine`` 协议注入。
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config.loader import load_config
from ..config.schema import EngineKind, GPUConfig
from ..core.errors import ConfigError, ZJY14Error
from ..core.logging import get_logger, setup_logging
from ..core.paths import ensure_dirs
from ..db.migrate import migrate
from ..db.session import Database
from ..inference.engine import TTSEngine
from .middleware import RequestIdMiddleware
from .routes import characters, health, speakers, synthesis, system
from .state import AppState

logger = get_logger("app")


def create_engine(config: GPUConfig) -> TTSEngine:
    """按配置创建引擎实例。

    Phase 1 只有 stub。Phase 2 加 cosyvoice3 分支。
    """
    if config.engine is EngineKind.STUB:
        from ..inference.stub_engine import StubEngine

        return StubEngine(config)

    if config.engine is EngineKind.COSYVOICE3:
        from ..inference.cosyvoice_engine import CosyVoiceEngine

        return CosyVoiceEngine(config)

    raise ConfigError(f"未知引擎类型: {config.engine!r}")


def check_vram_margin(config: GPUConfig) -> None:
    """启动时检查显存余量（决策：显存不足时明确报错，不静默崩溃）。

    torch 不可用或 device=cpu 时跳过。
    GPU 探测封装在 inference.gpu_info 中，本层不直接依赖 torch。
    """
    if config.device != "cuda":
        return

    from ..inference.gpu_info import free_vram_mb, gpu_info

    info = gpu_info()
    if not info.get("ok"):
        logger.warning(
            "无法读取显存，跳过显存检查",
            extra={
                "extra_fields": {
                    "reason": info.get("reason", "unknown"),
                    "required_margin_mb": config.memory_safety_margin_mb,
                }
            },
        )
        return

    free_mb = free_vram_mb()
    if free_mb is None:
        logger.warning("无法读取显存，跳过显存检查")
        return

    if free_mb < config.memory_safety_margin_mb:
        from ..core.errors import VRAMError

        raise VRAMError(
            f"可用显存不足：需要至少 {config.memory_safety_margin_mb} MB，"
            f"当前仅 {free_mb:.0f} MB（总 {info.get('total_mb', 0)} MB）。"
            "请关闭占用显存的程序，或调低 configs/gpu.yaml 的 memory_safety_margin_mb。",
            detail={"free_mb": round(free_mb), "required_mb": config.memory_safety_margin_mb},
        )
    logger.info(
        "显存检查通过",
        extra={
            "extra_fields": {
                "free_mb": round(free_mb),
                "total_mb": info.get("total_mb", 0),
                "required_mb": config.memory_safety_margin_mb,
            }
        },
    )


def build_app(config: GPUConfig | None = None) -> FastAPI:
    """构造 FastAPI 应用。

    :param config: 显式传入配置（测试用）；None 则从 configs/gpu.yaml 加载
    """
    cfg = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging()
        ensure_dirs()
        logger.info(
            "启动 zjy14 Custom Voice Engine",
            extra={
                "extra_fields": {
                    "engine": cfg.engine.value,
                    "device": cfg.device,
                    "precision": cfg.precision.value,
                    "host": cfg.host,
                    "port": cfg.port,
                }
            },
        )

        check_vram_margin(cfg)

        db = Database()
        migrate(db)

        engine = create_engine(cfg)
        engine.load()

        # speaker 条件缓存（决策：实测省 16.5% 合成耗时）
        from ..speaker.cache import SpeakerConditionCache

        speaker_cache = SpeakerConditionCache(
            max_items=cfg.speaker_cache_max_items,
            enabled=cfg.speaker_cache_enabled,
        )

        # 推理调度器：并发限制 + 排队 + 取消
        from ..inference.scheduler import Scheduler

        scheduler = Scheduler(
            max_concurrent=cfg.max_concurrent_requests,
            max_queue=cfg.max_queue_size,
            queue_timeout_s=cfg.queue_timeout_s,
        )
        logger.info(
            "推理调度器已启用",
            extra={
                "extra_fields": {
                    "max_concurrent": cfg.max_concurrent_requests,
                    "max_queue": cfg.max_queue_size,
                    "queue_timeout_s": cfg.queue_timeout_s,
                }
            },
        )

        app.state.zjy14 = AppState(cfg, engine, db, speaker_cache, scheduler)
        try:
            yield
        finally:
            engine.shutdown()
            db.close()
            logger.info("服务已关闭")

    app = FastAPI(
        title="zjy14 Custom Voice Engine",
        version="0.1.0",
        description="CosyVoice3 角色音色与情绪表达控制引擎",
        lifespan=lifespan,
    )

    app.add_middleware(RequestIdMiddleware)

    # ---- 异常处理：统一转 JSON，不泄露堆栈 ----
    @app.exception_handler(ZJY14Error)
    async def _handle_zjy14_error(request: Request, exc: ZJY14Error) -> JSONResponse:
        logger.warning(
            "请求失败",
            extra={
                "extra_fields": {
                    "path": request.url.path,
                    "code": exc.code,
                    "status": exc.http_status,
                    "message": exc.message,
                }
            },
        )
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "未预期异常",
            extra={"extra_fields": {"path": request.url.path}},
        )
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "服务内部错误，请查看日志"},
        )

    # ---- 路由 ----
    app.include_router(health.router)
    app.include_router(system.router)
    app.include_router(synthesis.router)
    app.include_router(characters.router)
    app.include_router(speakers.router)

    return app


def state_of(app: FastAPI) -> AppState:
    """取应用状态，未初始化时抛明确错误。

    保留此别名是为了兼容旧调用；新代码请用 ``deps.get_state``。
    """
    from .deps import state_of_app

    return state_of_app(app)


__all__ = [
    "build_app",
    "check_vram_margin",
    "create_engine",
    "state_of",
]
