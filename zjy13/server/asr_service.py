"""ASR 独立 HTTP 服务（仅标准库，零新增依赖）。

## 为什么需要它

zjy13 原本把 FunASR **内嵌在 WebSocket 服务里**（``server/ws/server.py``
启动时 `ASREngine(...).load()`）。这带来两个问题：

1. **无法独立启动/重启**：ASR 出问题必须重启整个 WS 服务，板子连接全断。
2. **无法独立验证**：想单测识别效果，只能起整套服务。

本服务把 ASR 暴露成独立进程，WS 服务可以通过 `ASR_SERVICE_URL`
把识别请求转过来（见 ``server/ws/server.py`` 的 ``--asr-url``）。
模型只加载一份，显存不会翻倍。

## 为什么用标准库而不是 FastAPI

``.venv-asr`` 里**没有** fastapi / uvicorn（实测 `find_spec` 均为 False），
装它们会引入不必要的依赖面。本服务只有两个端点，``http.server`` 足够。

## 端点

    GET  /health      引擎/设备/模型/加载耗时
    POST /transcribe  识别一段音频
                      - ``Content-Type: audio/*`` 或 octet-stream：body 是 WAV 字节
                      - ``application/json``：``{"audio_path": "..."}``
                      返回 ``{"text": ..., "duration_s": ..., ...}``

## 用法

    .venv-asr\\Scripts\\python.exe -m server.asr_service --port 50002
    .venv-asr\\Scripts\\python.exe -m server.asr_service --port 50002 --preload

``--preload`` 启动时就加载模型（健康检查能立刻反映「已就绪」），
否则首次识别时才加载（启动快，但首个请求要等 2 秒左右）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger("asr.service")

#: 单次请求体上限（30 秒 16k 单声道 16bit ≈ 960KB，留足余量）。
MAX_BODY_BYTES = 8 * 1024 * 1024

#: 超限请求最多丢弃多少字节再回 413（防止超大上传拖住线程）。
MAX_DRAIN_BYTES = 32 * 1024 * 1024

_KIND = "zjy13-asr-service"


class _EngineHolder:
    """引擎单例 + 线程安全加载。

    FunASR 的 ``generate`` 不是线程安全的（共享模型内部状态），
    所以识别必须串行化。这里用一把锁同时保护「加载」与「识别」。
    """

    def __init__(self, *, preload: bool, use_vad: bool, device: str) -> None:
        self._lock = threading.Lock()
        self._engine: Any = None
        self._engine_error: str | None = None
        self._use_vad = use_vad
        self._device = device
        self._load_started = time.perf_counter()
        if preload:
            try:
                self.ensure_loaded()
            except Exception:  # noqa: BLE001
                # 预加载失败不退出：健康检查要能把原因报出来
                logger.exception("ASR 预加载失败")

    def ensure_loaded(self) -> Any:
        """加载引擎（幂等）。失败时缓存错误，避免每次请求都重试加载。"""
        if self._engine is not None:
            return self._engine
        if self._engine_error is not None:
            raise RuntimeError(self._engine_error)
        with self._lock:
            if self._engine is not None:
                return self._engine
            try:
                from asr.engine import ASREngine

                engine = ASREngine(use_vad=self._use_vad, device=self._device)
                engine.load()
                self._engine = engine
                logger.info("ASR 模型加载完成，耗时 %.0f ms", engine.load_ms)
                return engine
            except Exception as exc:  # noqa: BLE001
                self._engine_error = f"{type(exc).__name__}: {exc}"
                logger.exception("ASR 引擎加载失败")
                raise

    def transcribe_wav_bytes(self, wav: bytes) -> Any:
        """识别 WAV 字节流。"""
        engine = self.ensure_loaded()
        # 临时文件放在引擎缓存目录下（``_models/_work``，已 gitignore），
        # 而不是 ``tempfile.gettempdir()`` —— 实测本机 gettempdir() 会
        # 落到**当前工作目录**（Temp 目录探测失败时 Python 的回退行为），
        # 那样每次请求都会在仓库根留下 ``asr_svc_*`` 目录。
        work_root = Path(getattr(engine, "cache_dir", None) or ".") / "_work"
        work_root.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="asr_svc_", dir=str(work_root)))
        wav_path = tmp_dir / "in.wav"
        try:
            wav_path.write_bytes(wav)
            with self._lock:
                return engine.transcribe_file(wav_path)
        finally:
            try:
                wav_path.unlink(missing_ok=True)
                tmp_dir.rmdir()
            except OSError:
                pass

    def transcribe_path(self, audio_path: str) -> Any:
        engine = self.ensure_loaded()
        with self._lock:
            return engine.transcribe_file(audio_path)

    def health(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "ok": True,
            "kind": _KIND,
            "loaded": self._engine is not None,
            "error": self._engine_error,
            "uptime_s": round(time.perf_counter() - self._load_started, 1),
        }
        if self._engine is not None:
            try:
                # DeviceInfo 的字段名以 asr/engine.py 为准（实测踩过：
                # 曾误用 dev.device / dev.gpu_name，健康检查里只留下
                #「'DeviceInfo' object has no attribute 'device'」）。
                dev = self._engine.device_info
                info.update({
                    "device": dev.device_name,
                    "using_gpu": dev.using_gpu,
                    "gpu": dev.device_name,
                    "torch": dev.torch_version,
                    "cuda": dev.cuda_version,
                    "arch_match": dev.arch_match,
                    "model": self._engine.model_id,
                    "load_ms": round(self._engine.load_ms, 1),
                    "warmup_ms": (
                        round(self._engine.warmup_ms, 1)
                        if self._engine.warmup_ms is not None
                        else None
                    ),
                })
            except Exception as exc:  # noqa: BLE001
                info["error"] = f"读取引擎信息失败: {exc}"
        return info


class _Handler(BaseHTTPRequestHandler):
    """极简路由。只支持 health / transcribe。"""

    server_version = "zjy13-asr/1.0"
    #: 由 create_server 注入
    holder: _EngineHolder

    # ---- 工具 ----

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 本服务只监听回环，跨端口调用（zjy14 前端 / WS 服务）需 CORS
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        # 默认实现会往 stderr 打一行一条，识别请求多时刷屏；
        # 交给 logging 统一处理（级别可调）。
        logger.debug("%s - %s", self.address_string(), fmt % args)

    # ---- 路由 ----

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_json(200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ("/health", "/"):
            self._send_json(200, self.holder.health())
            return
        self._send_json(404, {"ok": False, "error": f"未知路径 {path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path != "/transcribe":
            self._send_json(404, {"ok": False, "error": f"未知路径 {path}"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._send_json(400, {"ok": False, "error": "请求体为空"})
            return
        if length > MAX_BODY_BYTES:
            # 必须把请求体**读完再回**（有上限地丢弃）。
            #
            # 实测踩过：只发响应不读 body，服务端 close 时 socket 里还有
            # 未读数据，Windows 会直接 RST —— 客户端拿到的是
            # ``ConnectionAbortedError`` 而不是 413，排查时会误以为是网络问题。
            # 读取上限保证不会因为超大上传把内存/时间吃满。
            drained = 0
            budget = min(length, MAX_DRAIN_BYTES)
            while drained < budget:
                chunk = self.rfile.read(min(64 * 1024, budget - drained))
                if not chunk:
                    break
                drained += len(chunk)
            self.close_connection = True
            self._send_json(
                413,
                {
                    "ok": False,
                    "error": f"请求体过大（{length} > {MAX_BODY_BYTES}）",
                },
            )
            return

        body = self.rfile.read(length)
        ctype = (self.headers.get("Content-Type") or "").lower()

        started = time.perf_counter()
        try:
            if "json" in ctype:
                payload = json.loads(body.decode("utf-8"))
                audio_path = str(payload.get("audio_path") or "").strip()
                if not audio_path:
                    self._send_json(
                        400, {"ok": False, "error": "缺少 audio_path 字段"}
                    )
                    return
                result = self.holder.transcribe_path(audio_path)
            else:
                result = self.holder.transcribe_wav_bytes(body)
        except RuntimeError as exc:
            # 引擎加载失败（已缓存原因）
            self._send_json(503, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("识别失败")
            self._send_json(
                500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )
            return

        out = result.to_dict()
        out["ok"] = True
        out["service_ms"] = round((time.perf_counter() - started) * 1000, 1)
        self._send_json(200, out)


def create_server(host: str, port: int, holder: _EngineHolder) -> ThreadingHTTPServer:
    """构造服务器（把 holder 绑到 handler 类上）。"""
    handler = type("_BoundHandler", (_Handler,), {"holder": holder})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ASR 独立 HTTP 服务（FunASR Paraformer）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅回环）")
    ap.add_argument("--port", type=int, default=50002, help="监听端口")
    ap.add_argument(
        "--preload", action="store_true", help="启动时立即加载模型"
    )
    ap.add_argument("--use-vad", action="store_true", help="启用 VAD")
    ap.add_argument(
        "--device", default="cuda:0", help="推理设备（cuda:0 / cpu）"
    )
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # asr 包在 zjy13 根目录下（本文件是 server/asr_service.py）
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    holder = _EngineHolder(
        preload=args.preload, use_vad=args.use_vad, device=args.device
    )
    httpd = create_server(args.host, args.port, holder)
    logger.info(
        "ASR 服务已启动 http://%s:%d（preload=%s device=%s）",
        args.host, args.port, args.preload, args.device,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，关闭")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    # MODELSCOPE_CACHE 等在 engine.load() 里设置；这里提前给一个可写目录，
    # 避免受限环境下 ModelScope 写用户目录被拒。
    os.environ.setdefault(
        "MODELSCOPE_CACHE",
        str(Path(__file__).resolve().parent.parent / "_models"),
    )
    raise SystemExit(main())