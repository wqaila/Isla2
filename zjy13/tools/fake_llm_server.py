"""本地 OpenAI 兼容假服务端（仅用于验证客户端代码路径，不代表真实云端行为）。

用途：在没有任何真实 API 密钥的情况下，验证流式解析、非流式解析、错误映射、
重试、取消等逻辑确实能跑通。它**不是**服务商模拟器，也不产生真实模型回复。

启动：
    python -m tools.fake_llm_server --port 8765

然后：
    $env:LLM_BASE_URL="http://127.0.0.1:8765/v1"
    $env:LLM_API_KEY="local-test-key"
    $env:LLM_MODEL="fake-model"
    python -m tools.llm_smoke "你好"
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

REPLY = "嗯，我想想。这是本地假服务端返回的测试文本，不是真实模型输出。"


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # 由 main() 注入
    scenario: str = "stream"
    delay_s: float = 0.05

    def log_message(self, fmt: str, *args: Any) -> None:  # 静音默认日志
        return

    # ------------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802 - http.server 约定
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return self._json(400, {"error": {"message": "invalid json", "code": "bad_json"}})

        scenario = self.scenario
        if scenario == "auth_error":
            return self._json(401, {"error": {"message": "invalid api key", "code": "invalid_api_key"}})
        if scenario == "rate_limit":
            return self._json(
                429,
                {"error": {"message": "rate limit exceeded", "code": "rate_limit"}},
                headers={"Retry-After": "0.2"},
            )
        if scenario == "server_error":
            return self._json(500, {"error": {"message": "internal error", "code": "server_error"}})
        if scenario == "slow":
            time.sleep(5)
        if scenario == "bad_json":
            return self._raw(200, b"not-json", "application/json")

        if body.get("stream"):
            return self._stream(body)
        return self._blocking(body)

    # ------------------------------------------------------------------
    def _stream(self, body: dict[str, Any]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def chunk(data: bytes) -> None:
            self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
            self.wfile.flush()

        try:
            # 逐字切片，模拟真实流式
            pieces = [REPLY[i : i + 4] for i in range(0, len(REPLY), 4)]
            for piece in pieces:
                chunk(
                    _sse(
                        {
                            "id": "chatcmpl-fake",
                            "object": "chat.completion.chunk",
                            "model": body.get("model", "fake-model"),
                            "choices": [
                                {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                            ],
                        }
                    )
                )
                time.sleep(self.delay_s)
            chunk(
                _sse(
                    {
                        "id": "chatcmpl-fake",
                        "object": "chat.completion.chunk",
                        "model": body.get("model", "fake-model"),
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
                )
            )
            if (body.get("stream_options") or {}).get("include_usage"):
                chunk(
                    _sse(
                        {
                            "id": "chatcmpl-fake",
                            "object": "chat.completion.chunk",
                            "model": body.get("model", "fake-model"),
                            "choices": [],
                            "usage": {
                                "prompt_tokens": 100,
                                "completion_tokens": 20,
                                "total_tokens": 120,
                            },
                        }
                    )
                )
            chunk(b"data: [DONE]\n\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _blocking(self, body: dict[str, Any]) -> None:
        self._json(
            200,
            {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": body.get("model", "fake-model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": REPLY},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
        )

    # ------------------------------------------------------------------
    def _json(self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._raw(status, data, "application/json; charset=utf-8", headers)

    def _raw(
        self,
        status: int,
        data: bytes,
        content_type: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()


class FakeLLMServer:
    """可编程启动/关闭的本地假服务端，便于测试和手工验证。"""

    def __init__(self, *, port: int = 0, scenario: str = "stream", delay_s: float = 0.05) -> None:
        handler = type("BoundHandler", (Handler,), {"scenario": scenario, "delay_s": delay_s})
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self) -> "FakeLLMServer":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="本地 OpenAI 兼容假服务端（验证用）")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--scenario",
        default="stream",
        choices=["stream", "auth_error", "rate_limit", "server_error", "slow", "bad_json"],
    )
    parser.add_argument("--delay", type=float, default=0.05, help="每个片段的间隔秒数")
    args = parser.parse_args()

    server = FakeLLMServer(port=args.port, scenario=args.scenario, delay_s=args.delay)
    print(f"假服务端已启动: {server.base_url}  scenario={args.scenario}")
    print("注意：这是本地测试桩，不产生真实模型输出。Ctrl+C 结束。")
    try:
        server._thread.start()  # noqa: SLF001 - 脚本入口
        server._thread.join()  # noqa: SLF001
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
