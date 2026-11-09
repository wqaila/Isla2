"""路由层依赖注入。

单独成模块是为了打破循环导入：
    app.py -> routes/health.py -> app.py (state_of)

现在路由只依赖本模块，app.py 也从这里取。
"""

from __future__ import annotations

import uuid

from fastapi import Request

from ..core.errors import EngineNotReadyError
from .state import AppState


def state_of_app(app) -> AppState:  # type: ignore[no-untyped-def]
    """从 FastAPI 实例取应用状态，未初始化时抛明确错误。"""
    state = getattr(app.state, "zjy14", None)
    if state is None:
        raise EngineNotReadyError("应用尚未完成启动")
    return state


def get_state(request: Request) -> AppState:
    """FastAPI 依赖：从请求取应用状态。"""
    return state_of_app(request.app)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


__all__ = ["get_state", "new_request_id", "state_of_app"]
