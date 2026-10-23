"""本地设置页：在浏览器里填写 DeepSeek 等云端配置。

安全边界：
  - 只有本机（127.0.0.1 / ::1）可以访问，局域网访问一律拒绝；
  - 写操作需要管理令牌，放在 HttpOnly Cookie 或 X-Admin-Token 头；
  - 密钥只写入本机 .env，接口从不回传完整密钥；
  - 不把密钥写进日志或错误信息。
"""
from __future__ import annotations

import html
import hmac
import json
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import httpx
from pydantic import BaseModel, Field

COOKIE_NAME = "zjy11_admin"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}

# 允许写入 .env 的键，白名单以外的键一律不落盘
ALLOWED_KEYS = {
    "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "GATEWAY_CLOUD_ENABLED",
    "GATEWAY_DEVICE_TOKEN", "LLM_TEMPERATURE", "LLM_MAX_TOKENS",
    "LLM_TIMEOUT_SECONDS", "LLM_TRUST_ENV", "GATEWAY_RATE_LIMIT",
    "GATEWAY_HOST", "GATEWAY_PORT",
}


class SaveInput(BaseModel):
    api_key: str = Field(default="")
    model: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=200)
    enabled: bool = False


class TestInput(BaseModel):
    api_key: str = Field(default="")
    model: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=200)


def _is_local(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in LOCAL_HOSTS or host.startswith("127.")


def _read_env(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        if key in ALLOWED_KEYS:
            values[key] = raw.strip()
    return values


def _write_env(path: Path, updates: dict) -> None:
    """只改白名单内的键，保留注释与其他行。"""
    lines = []
    remaining = dict(updates)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in remaining:
                    lines.append(f"{key}={remaining.pop(key)}")
                    continue
            lines.append(line)
    for key, value in remaining.items():
        lines.append(f"{key}={value}")

    tmp = path.with_suffix(".env.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # 原子替换，避免写一半断电留下损坏配置


def _mask(key: str) -> str:
    if not key:
        return ""
    return ("*" * max(0, len(key) - 4)) + key[-4:]


from .settings_page_template import SETTINGS_HTML as PAGE


def _guard_factory(settings):
    """构造路由级鉴权依赖：先验本机来源与管理令牌，再校验请求体。

    这一步不能写在每个处理函数里 —— 那样 FastAPI 会先做请求体校验，
    未认证请求就会拿到 422 而不是 401，等于泄露了「参数格式」这一信息。
    """
    admin_token = getattr(settings, "admin_token", "") or ""

    def guard(request: Request, x_admin_token: str | None = Header(default=None)):
        _require_local(request)
        supplied = x_admin_token or request.cookies.get(COOKIE_NAME) or ""
        if not admin_token or not hmac.compare_digest(supplied, admin_token):
            raise HTTPException(status_code=401, detail="管理令牌不正确")

    return guard


def _require_local(request: Request) -> None:
    if not _is_local(request):
        raise HTTPException(status_code=403, detail="仅允许本机访问设置页")


def create_settings_router(settings, root: Path):
    root = Path(root)
    # 页面本身和 /login 必须匿名可达（它们就是登录入口）；
    # 其余接口用路由级依赖鉴权，保证先返回 401 再做请求体校验。
    router = APIRouter(prefix="/settings")
    protected = APIRouter(dependencies=[Depends(_guard_factory(settings))])

    @router.get("", response_class=HTMLResponse)
    async def page(request: Request):
        _require_local(request)
        return HTMLResponse(PAGE)

    @router.post("/login")
    async def login(request: Request):
        _require_local(request)
        body = await request.json()
        supplied = str(body.get("token") or "")
        admin_token = getattr(settings, "admin_token", "") or ""
        if not admin_token or not hmac.compare_digest(supplied, admin_token):
            raise HTTPException(status_code=401, detail="管理令牌不正确")
        resp = JSONResponse({"status": "ok"})
        # HttpOnly + SameSite=Strict：脚本读不到，跨站请求也带不出去
        resp.set_cookie(COOKIE_NAME, admin_token, httponly=True, samesite="strict", path="/settings")
        return resp

    @router.post("/logout")
    async def logout(request: Request):
        _require_local(request)
        resp = JSONResponse({"status": "ok"})
        resp.delete_cookie(COOKIE_NAME, path="/settings")
        return resp

    @protected.get("/state")
    async def state(request: Request):
        env = _read_env(root / ".env")
        key = env.get("LLM_API_KEY", "")
        return {
            "base_url": env.get("LLM_BASE_URL", ""),
            "model": env.get("LLM_MODEL", ""),
            "enabled": env.get("GATEWAY_CLOUD_ENABLED", "false").lower() in {"true", "1"},
            "api_key_set": bool(key),
            "api_key_hint": _mask(key)[-4:] if key else "",
        }

    @protected.post("/save")
    async def save(body: SaveInput, request: Request):
        if not body.base_url.startswith("https://"):
            raise HTTPException(status_code=422, detail="接口地址必须使用 https")

        updates = {
            "LLM_BASE_URL": body.base_url.strip(),
            "LLM_MODEL": body.model.strip(),
            "GATEWAY_CLOUD_ENABLED": "true" if body.enabled else "false",
        }
        if body.api_key.strip():
            updates["LLM_API_KEY"] = body.api_key.strip()
        elif not _read_env(root / ".env").get("LLM_API_KEY"):
            # 首次保存必须有密钥，否则保存了个空配置毫无意义
            raise HTTPException(status_code=422, detail="首次保存必须填写 API Key")

        _write_env(root / ".env", updates)
        return {"status": "ok", "message": "已保存，重启网关后生效"}

    @protected.post("/test")
    async def probe(body: TestInput, request: Request):
        """真实调用一次极短请求验证凭据，不写入配置、不回显密钥。"""
        if not body.base_url.startswith("https://"):
            raise HTTPException(status_code=422, detail="接口地址必须使用 https")

        key = body.api_key.strip() or _read_env(root / ".env").get("LLM_API_KEY", "")
        if not key:
            raise HTTPException(status_code=422, detail="需要 API Key 才能测试")

        payload = {
            "model": body.model.strip(),
            "messages": [{"role": "user", "content": "你好"}],
            "max_tokens": 8,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                resp = await client.post(
                    body.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.HTTPError:
            return JSONResponse({"ok": False, "message": "无法连接该接口，请检查网络或地址"}, status_code=502)

        if resp.status_code == 200:
            return {"ok": True, "message": "连接成功，模型可调用"}
        if resp.status_code in (401, 403):
            return JSONResponse({"ok": False, "message": "密钥无效或没有该模型权限"}, status_code=502)
        if resp.status_code == 429:
            return JSONResponse({"ok": False, "message": "额度不足或被限流"}, status_code=502)
        if resp.status_code == 404:
            return JSONResponse({"ok": False, "message": "模型标识或接口地址不正确"}, status_code=502)
        return JSONResponse({"ok": False, "message": f"接口返回 {resp.status_code}"}, status_code=502)

    router.include_router(protected)
    return router
