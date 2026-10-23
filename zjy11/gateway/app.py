"""单进程网关：短期会话、去重和真实云端调用；没有模拟回复模式。"""
import asyncio
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import secrets
import time
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings, load_settings


class GatewayError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    session_id: str | None = Field(default=None, max_length=80)
    message: str

    @field_validator("message")
    @classmethod
    def validate_message(cls, value):
        value = value.strip()
        if not value or len(value.encode("utf-8")) > 512:
            raise ValueError("消息必须非空，且不超过 512 个 UTF-8 字节。")
        return value


class ResetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=80)


@dataclass
class Session:
    turns: deque = field(default_factory=lambda: deque(maxlen=4))
    touched: float = field(default_factory=time.monotonic)


@dataclass
class Receipt:
    fingerprint: str
    session_id: str
    created: float
    status: int | None = None
    body: dict | None = None


class State:
    def __init__(self):
        self.sessions = {}
        self.receipts = OrderedDict()
        self.call_times = deque()
        self.busy = False

    def prune(self):
        now = time.monotonic()
        for key in list(self.receipts):
            receipt = self.receipts[key]
            if receipt.status is not None and now - receipt.created > 600:
                del self.receipts[key]
        for key in list(self.sessions):
            if now - self.sessions[key].touched > 3600:
                del self.sessions[key]
        while self.call_times and now - self.call_times[0] > 60:
            self.call_times.popleft()


def error_body(code, message, request_id=None):
    result = {"error": {"code": code, "message": message}}
    if request_id:
        result["request_id"] = request_id
    return result


def response(body, status=200):
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def messages_for(card, session, message):
    # 历史只按完整轮次裁剪；示例与用户真实历史在系统说明中区分。
    turns = list(session.turns)
    while sum(len(m["content"]) for turn in turns for m in turn) > 1600:
        turns.pop(0)
    history = [dict(m) for turn in turns for m in turn]
    return [{"role": "system", "content": card["system_prompt"]}, *card["examples"],
            *history, {"role": "user", "content": message}]


async def call_cloud(client, settings, messages):
    payload = {
        "model": settings.model, "messages": messages, "stream": False,
        "temperature": settings.temperature, "max_tokens": settings.max_tokens,
    }
    try:
        async with asyncio.timeout(settings.timeout):
            async with client.stream(
                "POST", settings.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {settings.api_key}", "Content-Type": "application/json"},
                json=payload,
            ) as result:
                if result.status_code in (401, 403):
                    raise GatewayError(502, "cloud_auth_failed", "云端鉴权失败，请在电脑端检查 API Key 与模型权限。")
                if result.status_code == 429:
                    raise GatewayError(503, "cloud_rate_limited", "云端限流或额度不足，请检查服务商状态。")
                if result.status_code != 200:
                    raise GatewayError(502, "cloud_http_error", "云端请求失败，请检查服务商状态和模型配置。")
                raw = bytearray()
                async for chunk in result.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise GatewayError(502, "cloud_response_too_large", "云端响应超过网关限制。")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise GatewayError(502, "cloud_response_invalid", "云端响应必须是 JSON 对象。")
        choice = data["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise GatewayError(502, "cloud_response_invalid", "云端回答结构无效。")
        if choice.get("finish_reason") == "length":
            raise GatewayError(502, "cloud_reply_truncated", "云端回答被长度限制截断，请调整输出预算。")
        if choice.get("finish_reason") not in (None, "stop"):
            raise GatewayError(502, "cloud_reply_incomplete", "云端未返回完整文字回答。")
        reply = choice["message"]["content"]
        if not isinstance(reply, str) or not reply.strip() or len(reply) > 1000:
            raise GatewayError(502, "cloud_reply_invalid", "云端回答为空、格式无效或过长。")
        usage_raw = data.get("usage")
        usage = None
        if isinstance(usage_raw, dict):
            usage = {key: usage_raw[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                     if type(usage_raw.get(key)) is int and usage_raw[key] >= 0} or None
        return reply.strip(), usage
    except (TimeoutError, httpx.TimeoutException):
        raise GatewayError(504, "cloud_timeout", "云端响应超时，本请求不会自动重试。") from None
    except httpx.HTTPError:
        raise GatewayError(502, "cloud_network_error", "无法连接云端，请检查网络或显式代理配置。") from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise GatewayError(502, "cloud_response_invalid", "云端返回的格式不符合文字对话接口要求。") from None


def create_app(settings: Settings | None = None, client: httpx.AsyncClient | None = None):
    settings = settings or load_settings()
    owned_client = client is None
    upstream = client or httpx.AsyncClient(
        timeout=settings.timeout, trust_env=settings.trust_env, follow_redirects=False,
    )
    state = State()
    card_path = Path(__file__).with_name("character.json")
    card = json.loads(card_path.read_text(encoding="utf-8"))
    version = hashlib.sha256(card_path.read_bytes()).hexdigest()[:12]

    @asynccontextmanager
    async def lifespan(app):
        yield
        if owned_client:
            await upstream.aclose()

    app = FastAPI(title="zjy11 角色对话网关", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def request_limits(request, call_next):
        if request.url.path.startswith("/v1/") and request.method == "POST":
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > 2048):
                return response(error_body("request_too_large", "请求体不能超过 2 KiB。"), 413)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 2048:
                    return response(error_body("request_too_large", "请求体不能超过 2 KiB。"), 413)
            # Starlette 缓存 body，后续验证读取相同内容，不重复消费流。
            request._body = bytes(body)
        result = await call_next(request)
        result.headers["Cache-Control"] = "no-store"
        result.headers["X-Content-Type-Options"] = "nosniff"
        return result

    @app.exception_handler(GatewayError)
    async def known_error(request, exc):
        return response(error_body(exc.code, exc.message), exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # 不回显 Pydantic 的原始输入，避免密钥或正文进入错误响应。
        return response(error_body("invalid_request", "请求格式无效，请检查消息长度、标识和允许的字段。"), 422)

    def authorize(authorization: Annotated[str | None, Header()] = None):
        expected = f"Bearer {settings.device_token}".encode("utf-8")
        if not secrets.compare_digest((authorization or "").encode("utf-8"), expected):
            raise GatewayError(401, "unauthorized", "设备鉴权失败。")

    @app.get("/health")
    async def health():
        reason = "ready" if settings.configured and settings.cloud_enabled else (
            "cloud_not_configured" if not settings.configured else "cloud_disabled")
        return {
            "status": "running", "ready": settings.configured and settings.cloud_enabled,
            "cloud_configured": settings.configured, "cloud_enabled": settings.cloud_enabled,
            "reason": reason, "character_id": card["id"], "character_version": version,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index():
        reason = "配置齐全，已允许模型调用（尚未验证云端连通性）。" if settings.configured and settings.cloud_enabled else (
            "云端 API 尚未配置；网关已运行，聊天返回 503。" if not settings.configured else "模型配置已加载，真实调用尚未开启。")
        return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>zjy11 网关状态</title></head>
<body><main><h1>zjy11 角色对话网关</h1><p>服务运行中。</p><p>{reason}</p>
<p>当前为朋友型爱莉希雅。设备接口：POST /v1/chat，需要设备令牌。</p>
<p><a href="/settings">打开设置页</a>（仅限本机访问，需要管理令牌）。</p>
<p>在项目目录查看 RUNNING.md 配置说明，或查看 <a href="/health">健康状态 JSON</a>。</p>
<p>尚未实现 ESP32 固件、屏幕显示和语音。</p></main></body></html>"""

    @app.post("/v1/chat", dependencies=[Depends(authorize)])
    async def chat(req: ChatInput):
        state.prune()
        fingerprint = hashlib.sha256(json.dumps(req.model_dump(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        previous = state.receipts.get(req.request_id)
        if previous:
            if previous.fingerprint != fingerprint:
                raise GatewayError(409, "request_id_conflict", "同一请求标识不能用于不同内容。")
            if previous.status is None:
                raise GatewayError(409, "request_pending", "该请求正在处理中，请勿重复提交。")
            return response(previous.body, previous.status)
        if not settings.configured:
            raise GatewayError(503, "cloud_not_configured", "请先配置云端 API 地址、模型与 API Key。")
        if not settings.cloud_enabled:
            raise GatewayError(503, "cloud_disabled", "真实云端调用尚未开启，请确认配置与费用后开启。")
        if state.busy:
            raise GatewayError(429, "gateway_busy", "网关正在处理另一条消息，请稍后再试。")
        if len(state.call_times) >= settings.rate_limit:
            raise GatewayError(429, "rate_limited", "本分钟新请求已达上限，请稍后再试。")
        if len(state.receipts) >= 256:
            raise GatewayError(429, "request_cache_full", "近期请求记录已满，请等待去重窗口过期。")
        if req.session_id:
            session = state.sessions.get(req.session_id)
            if session is None:
                raise GatewayError(404, "session_not_found", "会话不存在或已过期，请创建新会话。")
            session_id = req.session_id
        else:
            if len(state.sessions) >= 64:
                raise GatewayError(429, "session_limit", "会话数量已达上限，请等待旧会话过期。")
            session_id, session = secrets.token_urlsafe(24), Session()
        state.busy = True
        state.call_times.append(time.monotonic())
        receipt = Receipt(fingerprint, session_id, time.monotonic())
        state.receipts[req.request_id] = receipt
        started = time.monotonic()
        try:
            reply, usage = await call_cloud(upstream, settings, messages_for(card, session, req.message))
            body = {
                "request_id": req.request_id, "session_id": session_id, "reply": reply,
                "character_id": card["id"], "character_version": version, "model": settings.model,
                "response_ms": round((time.monotonic() - started) * 1000), "usage": usage,
            }
            if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > 8192:
                raise GatewayError(502, "response_too_large", "网关响应超过板端接收限制。")
            session.turns.append([{"role": "user", "content": req.message}, {"role": "assistant", "content": reply}])
            session.touched = time.monotonic()
            state.sessions[session_id] = session
            receipt.status, receipt.body = 200, body
            return response(body)
        except GatewayError as exc:
            receipt.status, receipt.body = exc.status, error_body(exc.code, exc.message, req.request_id)
            return response(receipt.body, receipt.status)
        finally:
            if receipt.status is None:
                receipt.status = 502
                receipt.body = error_body("request_interrupted", "请求中断，结果未知；不会自动重新调用模型。", req.request_id)
            state.busy = False

    @app.post("/v1/session/reset", dependencies=[Depends(authorize)])
    async def reset(req: ResetInput):
        if state.busy:
            raise GatewayError(429, "gateway_busy", "生成进行中，暂不能重置会话。")
        state.prune()
        session = state.sessions.get(req.session_id)
        if session is None:
            raise GatewayError(404, "session_not_found", "会话不存在或已过期。")
        session.turns.clear()
        session.touched = time.monotonic()
        for request_id, receipt in state.receipts.items():
            if receipt.session_id == req.session_id:
                receipt.status = 409
                receipt.body = error_body("session_reset", "此请求所属会话已重置，请使用新的请求标识。", request_id)
        return {"status": "reset", "session_id": req.session_id}

    # 设置页挂到同一应用：/settings 只能本机访问，需管理令牌
    from .settings_page import create_settings_router
    app.include_router(create_settings_router(settings, root=Path(__file__).resolve().parents[1]))

    return app
