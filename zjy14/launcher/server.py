"""启动控制台的 HTTP 服务（仅标准库）。

## 它解决什么问题

整套语音链路要起 3~4 个服务，横跨 Windows 与 WSL，原本只能靠记命令、
开多个终端。本服务提供一个网页控制台：一个按钮起全套，一个按钮停全套，
每个服务的健康状态、端口、日志都能直接看到。

## 为什么跑在 Windows 侧

ASR / 网关在 Windows（`.venv-asr`），TTS 在 WSL —— 只有 Windows 侧
才能同时拉起两边（用 `wsl.exe`）。所以本服务是**Windows 进程**。

## 为什么监听 127.0.0.1

这是「能启动本机任意进程」的工具，暴露到局域网等于交出本机控制权。
默认只绑回环；确需远程访问请自行改 `--host` 并清楚风险。

## 端点

    GET  /                       控制台页面（static/index.html）
    GET  /api/status             全部服务状态
    GET  /api/specs              服务定义（前端渲染用）
    POST /api/start/<name>       启动单个
    POST /api/stop/<name>        停止单个
    POST /api/start-all          一键启动（按依赖顺序）
    POST /api/stop-all           一键停止
    GET  /api/log/<name>?lines=N 日志尾部
    POST /api/llm/test           真实探测 DeepSeek 连通性（会消耗一次极小请求）

启动：

    python zjy14/launcher/server.py --port 50100
    python zjy14/launcher/server.py --port 50100 --engine stub   # TTS 用 stub
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# 允许直接 `python zjy14/launcher/server.py` 运行（不要求包上下文）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from launcher.orchestrator import (  # noqa: E402
    SERVICES,
    Orchestrator,
    inspect_llm_env,
)

logger = logging.getLogger("launcher")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class _Handler(BaseHTTPRequestHandler):
    server_version = "zjy14-launcher/1.0"

    #: 由 create_server 注入
    orch: Orchestrator
    engine: str = "cosyvoice3"

    # ---- 输出 ----

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 允许 zjy14 前端（:5173 / :50001）跨端口调用
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        logger.debug("%s - %s", self.address_string(), fmt % args)

    # ---- 路由 ----

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._json(200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in ("/", "/index.html"):
            page = STATIC_DIR / "index.html"
            if not page.is_file():
                self._json(500, {"ok": False, "error": "控制台页面缺失"})
                return
            self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            return

        if path == "/api/status":
            self._json(200, self.orch.status_all())
            return

        if path == "/api/specs":
            self._json(200, {
                "ok": True,
                "engine": self.engine,
                "services": [
                    {
                        "name": s.name,
                        "label": s.label,
                        "kind": s.kind,
                        "port": s.port,
                        "description": s.description,
                        "required": s.required,
                        "depends_on": list(s.depends_on),
                    }
                    for s in SERVICES
                ],
            })
            return

        if path == "/api/llm/env":
            self._json(200, {"ok": True, "llm": inspect_llm_env()})
            return

        if path.startswith("/api/log/"):
            name = path[len("/api/log/"):]
            qs = parse_qs(parsed.query)
            try:
                lines = int(qs.get("lines", ["60"])[0])
            except ValueError:
                lines = 60
            try:
                text = self.orch.read_log(name, lines=lines)
            except KeyError as exc:
                self._json(404, {"ok": False, "error": str(exc)})
                return
            self._json(200, {"ok": True, "name": name, "log": text})
            return

        self._json(404, {"ok": False, "error": f"未知路径 {path}"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # 读掉请求体（可能是 JSON，含 engine），避免连接残留
        length = int(self.headers.get("Content-Length") or 0)
        payload: dict[str, Any] = {}
        if length > 0:
            raw = self.rfile.read(min(length, 64 * 1024))
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:  # noqa: BLE001
                payload = {}

        engine = str(payload.get("engine") or self.engine)

        try:
            if path == "/api/start-all":
                self._json(200, {"ok": True, "result": self.orch.start_all(engine=engine)})
                return
            if path == "/api/stop-all":
                self._json(200, {"ok": True, "result": self.orch.stop_all()})
                return
            if path == "/api/llm/test":
                self._json(200, self._llm_test())
                return
            if path.startswith("/api/start/"):
                name = path[len("/api/start/"):]
                try:
                    res = self.orch.start(name, engine=engine)
                except KeyError as exc:
                    self._json(404, {"ok": False, "error": str(exc)})
                    return
                self._json(200, res)
                return
            if path.startswith("/api/stop/"):
                name = path[len("/api/stop/"):]
                try:
                    res = self.orch.stop(name)
                except KeyError as exc:
                    self._json(404, {"ok": False, "error": str(exc)})
                    return
                self._json(200, res)
                return
        except Exception as exc:  # noqa: BLE001
            logger.exception("操作失败")
            self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return

        self._json(404, {"ok": False, "error": f"未知路径 {path}"})

    # ---- LLM 真实连通性 ----

    def _llm_test(self) -> dict[str, Any]:
        """对 DeepSeek 发一次最小请求，确认「真的能用」。

        与 ``inspect_llm_env`` 的区别：那只做静态判定 + 代理端口探测，
        不发网络请求（不消耗配额、不会卡住数秒）。

        **必须按 .env 里的 LLM_PROXY 走代理**（2026-11-26 修）：
          第一版直接用 ``urllib.urlopen``，它只认 ``HTTPS_PROXY`` 环境变量、
          不认我们的 ``LLM_PROXY``，于是明明配了代理却走直连，
          在国内网络下必然超时 —— 实测返回「read timed out」，
          与网关实际能用（网关的 LLM 客户端会走代理、失败再回退直连）
          完全相反，属于**误报**。

        现在按 ``LLM_PROXY_FALLBACK`` 的语义：先试代理，失败再试直连，
        并把实际成功的路线报出来。单次超时压到 12 秒。
        """
        import urllib.error
        import urllib.request

        info = inspect_llm_env()
        if not info.get("ok"):
            return {"ok": False, "llm": info, "error": "配置不完整，先修 .env"}

        base = str(info.get("base_url") or "").rstrip("/")
        proxy = info.get("proxy")
        fallback = True
        env_file = Path(str(info.get("env_path")))
        api_key = ""
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s.startswith("LLM_API_KEY="):
                    api_key = s.split("=", 1)[1].strip()
                elif s.startswith("LLM_PROXY_FALLBACK="):
                    fallback = s.split("=", 1)[1].strip().lower() in ("1", "true", "yes")
        except OSError:
            pass

        url = base + "/models"
        # 依次尝试的路线：(标签, ProxyHandler 或 None)
        routes: list[tuple[str, Any]] = []
        if proxy:
            routes.append((f"代理 {proxy}", urllib.request.ProxyHandler({
                "http": proxy, "https": proxy,
            })))
            if fallback:
                routes.append(("直连（回退）", urllib.request.ProxyHandler({})))
        else:
            routes.append(("直连", None))

        errors: list[str] = []
        for label, handler in routes:
            opener = (
                urllib.request.build_opener(handler)
                if handler
                else urllib.request.build_opener()
            )
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {api_key}"}, method="GET"
            )
            started = time.perf_counter()
            try:
                with opener.open(req, timeout=12) as resp:
                    code = resp.status
                    resp.read(2048)
                return {
                    "ok": True,
                    "llm": info,
                    "route": label,
                    "status": code,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                }
            except urllib.error.HTTPError as exc:
                # 4xx 说明**连上了但密钥/路径不对**，换路线也没用
                return {
                    "ok": False,
                    "llm": info,
                    "route": label,
                    "error": f"HTTP {exc.code}（密钥无效或路径不对）",
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                }
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    f"{label}: {type(exc).__name__}: {exc} "
                    f"({round((time.perf_counter() - started) * 1000)}ms)"
                )

        return {
            "ok": False,
            "llm": info,
            "error": "所有路线都失败 —— " + "；".join(errors),
            "routes_tried": [r[0] for r in routes],
        }


def create_server(
    host: str, port: int, orch: Orchestrator, *, engine: str = "cosyvoice3"
) -> ThreadingHTTPServer:
    handler = type(
        "_BoundHandler", (_Handler,), {"orch": orch, "engine": engine}
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="zjy14 语音链路一键启动控制台")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅回环）")
    ap.add_argument("--port", type=int, default=50100, help="控制台端口")
    ap.add_argument(
        "--engine",
        default="cosyvoice3",
        choices=["cosyvoice3", "stub"],
        help="TTS 引擎（默认 cosyvoice3，即 zjy14 的真模型）",
    )
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument(
        "--open-browser", action="store_true", help="启动后自动打开浏览器"
    )
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    orch = Orchestrator()
    httpd = create_server(args.host, args.port, orch, engine=args.engine)
    url = f"http://{args.host}:{args.port}/"
    logger.info("启动控制台已就绪: %s（TTS 引擎=%s）", url, args.engine)

    if args.open_browser:
        def _open() -> None:
            time.sleep(0.8)
            try:
                import webbrowser

                webbrowser.open(url)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_open, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，关闭控制台（已启动的服务不会被停止）")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())