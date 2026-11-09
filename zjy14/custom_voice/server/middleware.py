"""request_id 注入中间件。

作用：
1. 为每个请求生成（或沿用调用方传入的）request_id
2. 塞进 contextvars，让日志自动带上
3. 回写到响应头 ``x-request-id``，便于客户端对账
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from ..core.logging import reset_request_id, set_request_id

HEADER = "x-request-id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        rid = request.headers.get(HEADER) or uuid.uuid4().hex[:16]
        token = set_request_id(rid)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers[HEADER] = rid
        return response


__all__ = ["HEADER", "RequestIdMiddleware"]
