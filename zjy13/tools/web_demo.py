"""本地网页对话演示：浏览器里直接操作，流式显示回复。

只用 Python 标准库（http.server）+ 已有的 httpx，不引入新依赖。

启动：
    .\\.venv\\Scripts\\python.exe -m tools.web_demo
或双击 启动网页演示.bat

安全：只监听 127.0.0.1，局域网其他设备无法访问。
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.config import LLMConfig, PROJECT_ROOT, load_env_file  # noqa: E402
from server.dialogue.character import CharacterCard  # noqa: E402
from server.dialogue.session import ConversationSession  # noqa: E402
from server.llm import LLMClient, LLMConfigError, LLMError  # noqa: E402

HOST = "127.0.0.1"


class DemoApp:
    """持有后台事件循环、LLM 客户端与会话状态。"""

    def __init__(self, cfg: LLMConfig, card: CharacterCard) -> None:
        self.cfg = cfg
        self.card = card
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.client: LLMClient | None = None
        self.session: ConversationSession | None = None
        self._init()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _init(self) -> None:
        async def build():
            self.client = LLMClient(self.cfg)
            self.client._ensure_client()  # noqa: SLF001 - 演示用，预热连接池
            self.session = ConversationSession(self.card, self.client)

        asyncio.run_coroutine_threadsafe(build(), self.loop).result(timeout=30)

    def reset(self) -> None:
        async def go():
            if self.session is not None:
                self.session.reset()

        asyncio.run_coroutine_threadsafe(go(), self.loop).result(timeout=10)

    def chat_stream(self, text: str):
        """同步生成器：逐块产出 SSE 数据。"""
        queue: asyncio.Queue = asyncio.Queue()
        loop = self.loop

        async def worker() -> None:
            assert self.session is not None
            try:
                record = await self.session.send(
                    text, on_delta=lambda t: loop.call_soon_threadsafe(queue.put_nowait, ("delta", t))
                )
                await queue.put(
                    (
                        "done",
                        {
                            "text": record.assistant_text,
                            "first_token_ms": round(record.first_token_ms, 1)
                            if record.first_token_ms
                            else None,
                            "total_ms": round(record.total_ms, 1),
                            "truncated": record.truncated,
                            "stats": self.session.stats(),
                        },
                    )
                )
            except LLMError as exc:
                await queue.put(("error", exc.to_dict()))
            except Exception as exc:  # noqa: BLE001 - 演示服务，任何异常都要告知前端
                await queue.put(("error", {"code": "internal", "message": str(exc)}))

        future = asyncio.run_coroutine_threadsafe(worker(), loop)
        while True:
            try:
                kind, payload = _wait_queue(queue, loop)
            except StopIteration:
                break
            yield kind, payload
            if kind in ("done", "error"):
                break
        future.result(timeout=60)

    def info(self) -> dict[str, Any]:
        return {
            "model": self.cfg.model,
            "thinking": self.cfg.thinking,
            "character": self.card.name,
            "character_id": self.card.character_id,
            "prompt_version": self.card.prompt_version(),
            "max_tokens": self.cfg.max_tokens,
            "system_prompt": self.card.system_prompt(),
            "examples": self.card.test_dialogues(),
            "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


def _wait_queue(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
    """从另一个线程安全地等待队列元素。"""
    fut = asyncio.run_coroutine_threadsafe(queue.get(), loop)
    try:
        return fut.result(timeout=120)
    except Exception:  # noqa: BLE001
        raise StopIteration from None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # 便于 SSE：发完即关闭连接
    app: DemoApp  # 由 main() 注入

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/info":
            self._send_json(200, self.app.info())
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid json"})
            return

        if self.path == "/api/reset":
            self.app.reset()
            self._send_json(200, {"ok": True})
            return

        if self.path == "/api/chat":
            text = str(body.get("text", "")).strip()
            if not text:
                self._send_json(400, {"error": "empty"})
                return
            self._stream_chat(text)
            return

        self._send_json(404, {"error": "not found"})

    # ------------------------------------------------------------------
    def _stream_chat(self, text: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            for kind, payload in self.app.chat_stream(text):
                data = json.dumps({"type": kind, "data": payload}, ensure_ascii=False)
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send(self, status: int, data: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")


PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>小满 · 云端 LLM 演示</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
         background:#f5f6f8; color:#222; height:100vh; display:flex; flex-direction:column; }
  header { background:#fff; border-bottom:1px solid #e5e7eb; padding:12px 20px;
           display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0; }
  .pill { font-size:12px; background:#eef2ff; color:#4a5bd6; padding:3px 9px; border-radius:20px; }
  .pill.gray { background:#f1f2f4; color:#666; }
  .spacer { flex:1; }
  button { font-size:13px; padding:6px 14px; border-radius:6px; border:1px solid #d5d8de;
           background:#fff; cursor:pointer; }
  button:hover { background:#f3f4f6; }
  button.primary { background:#4a5bd6; color:#fff; border-color:#4a5bd6; }
  button.primary:hover { background:#3d4cc0; }
  main { flex:1; overflow-y:auto; padding:22px; display:flex; flex-direction:column; gap:14px; }
  .msg { max-width:78%; padding:11px 14px; border-radius:10px; white-space:pre-wrap;
         line-height:1.65; font-size:14.5px; }
  .user { align-self:flex-end; background:#4a5bd6; color:#fff; }
  .bot { align-self:flex-start; background:#fff; box-shadow:0 1px 3px rgba(0,0,0,.08); }
  .bot.err { background:#fff1f1; color:#b42318; }
  .meta { font-size:11.5px; color:#9aa0a6; margin-top:6px; font-variant-numeric:tabular-nums; }
  .typing::after { content:"▍"; animation:blink 1s step-end infinite; color:#4a5bd6; }
  @keyframes blink { 50% { opacity:0; } }
  footer { background:#fff; border-top:1px solid #e5e7eb; padding:14px 20px;
           display:flex; gap:10px; align-items:flex-end; }
  textarea { flex:1; resize:none; height:52px; padding:12px; border-radius:8px;
             border:1px solid #d5d8de; font-family:inherit; font-size:14.5px; outline:none; }
  textarea:focus { border-color:#4a5bd6; }
  .hint { font-size:11.5px; color:#9aa0a6; padding:0 20px 8px; }
  dialog { border:none; border-radius:12px; padding:0; max-width:760px; width:90%;
           max-height:80vh; box-shadow:0 10px 40px rgba(0,0,0,.2); }
  dialog::backdrop { background:rgba(0,0,0,.35); }
  .dlg-head { padding:16px 20px; border-bottom:1px solid #eee; display:flex;
              align-items:center; justify-content:space-between; }
  .dlg-body { padding:18px 20px; overflow-y:auto; max-height:62vh; }
  pre { white-space:pre-wrap; font-size:12.5px; line-height:1.7; background:#f7f8fa;
        padding:14px; border-radius:8px; margin:0; }
</style></head><body>

<header>
  <h1>小满 · 云端 LLM 演示</h1>
  <span class="pill" id="pModel">-</span>
  <span class="pill gray" id="pThink">-</span>
  <span class="pill gray" id="pChar">-</span>
  <span class="spacer"></span>
  <button onclick="showCard()">查看角色卡</button>
  <button onclick="resetChat()">清空对话</button>
</header>

<main id="chat"></main>
<div class="hint">Enter 发送 · Shift+Enter 换行 · 真实调用云端 API，每轮会计费</div>

<footer>
  <textarea id="input" placeholder="跟小满说点什么……" autofocus></textarea>
  <button class="primary" id="sendBtn" onclick="send()">发送</button>
</footer>

<dialog id="cardDlg">
  <div class="dlg-head"><strong>角色卡与系统提示词</strong>
    <button onclick="document.getElementById('cardDlg').close()">关闭</button></div>
  <div class="dlg-body"><pre id="cardBody"></pre></div>
</dialog>

<script>
const chat = document.getElementById('chat');
const input = document.getElementById('input');
const sendBtn = document.getElementById('sendBtn');
let busy = false;

fetch('/api/info').then(r=>r.json()).then(info => {
  document.getElementById('pModel').textContent = info.model;
  document.getElementById('pThink').textContent = '思考: ' + info.thinking;
  document.getElementById('pChar').textContent = info.character + ' v' + info.prompt_version;
  window._info = info;
});

function addMsg(cls, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  div.textContent = text;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}
function addMeta(parent, text) {
  const m = document.createElement('div');
  m.className = 'meta';
  m.textContent = text;
  parent.appendChild(m);
  chat.scrollTop = chat.scrollHeight;
}

function showCard() {
  const info = window._info || {};
  const examples = (info.examples||[]).map(e=>'用户：'+e.user+'\\n你：'+e.assistant).join('\\n\\n');
  document.getElementById('cardBody').textContent =
    '【系统提示词】\\n' + (info.system_prompt||'') + '\\n\\n【示例对话 ' +
    (info.examples||[]).length + ' 组】\\n' + examples;
  document.getElementById('cardDlg').showModal();
}

async function resetChat() {
  if (busy) return;
  await fetch('/api/reset', {method:'POST', body:'{}'});
  chat.innerHTML = '';
  addMsg('bot', '（对话已清空，重新开始）');
}

async function send() {
  const text = input.value.trim();
  if (!text || busy) return;
  busy = true; sendBtn.disabled = true;
  input.value = '';

  addMsg('user', text);
  const bubble = addMsg('bot typing', '');
  const t0 = performance.now();

  try {
    const resp = await fetch('/api/chat', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({text})
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '', full = '';

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream:true});
      const parts = buf.split('\\n\\n');
      buf = parts.pop();
      for (const part of parts) {
        if (!part.startsWith('data: ')) continue;
        const evt = JSON.parse(part.slice(6));
        if (evt.type === 'delta') {
          full += evt.data;
          bubble.textContent = full;
          bubble.classList.add('typing');
          chat.scrollTop = chat.scrollHeight;
        } else if (evt.type === 'done') {
          bubble.classList.remove('typing');
          bubble.textContent = evt.data.text;
          const ft = evt.data.first_token_ms;
          addMeta(bubble, '首字 ' + (ft?ft+'ms':'—') + ' · 总 ' + evt.data.total_ms + 'ms' +
            (evt.data.truncated ? ' · 已截断' : ''));
        } else if (evt.type === 'error') {
          bubble.classList.remove('typing');
          bubble.classList.add('err');
          bubble.textContent = '[' + evt.data.code + '] ' + evt.data.message;
        }
      }
    }
  } catch (e) {
    bubble.classList.remove('typing');
    bubble.classList.add('err');
    bubble.textContent = '请求失败：' + e;
  } finally {
    busy = false; sendBtn.disabled = false; input.focus();
  }
}

input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
</script>
</body></html>"""


def main() -> int:
    try:
        load_env_file(PROJECT_ROOT / ".env")
    except LLMConfigError as exc:
        print(f"[配置错误] {exc}")
        return 2

    try:
        cfg = LLMConfig.from_env()
        cfg.validate()
    except LLMConfigError as exc:
        print(f"[配置错误] {exc}")
        return 2

    card = CharacterCard.from_file(PROJECT_ROOT / "server" / "characters" / "default.yaml")
    app = DemoApp(cfg, card)

    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((HOST, 8760), handler)
    url = f"http://{HOST}:8760"

    print("=" * 52)
    print("  小满 · 网页演示已启动")
    print("=" * 52)
    print(f"  地址: {url}")
    print(f"  模型: {cfg.model}   思考: {cfg.thinking}")
    print(f"  角色: {card.name} (v{card.prompt_version()})")
    print()
    print("  浏览器会自动打开。关闭本窗口即停止服务。")
    print("=" * 52)

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
