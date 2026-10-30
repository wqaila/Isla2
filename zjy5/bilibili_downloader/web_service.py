#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""B 站视频/音频采集 —— 本地网页服务（把已有 CLI 包一层 HTTP）。

【它是什么】
`python -m bilibili_downloader` 本身是个命令行工具。这个模块在它外面套一个
只监听 127.0.0.1 的 Flask 服务：浏览器里填链接 / 关键词，选「视频还是音频」，
后台跑 CLI，页面看实时进度和产物。**不新增任何下载逻辑**，能力完全来自 CLI。

【三个设计取舍】
1. **下载跑在子进程里，不在本进程 import 下载器**
   - `downloader.py` 的进度是 `print` 出来的，在同一个进程里想拿到这些行
     就得全局重定向 stdout（不是线程安全的，两个任务会串台）；
   - 走子进程还能在取消时**整棵进程树**杀掉（yt-dlp 会 fork ffmpeg 子进程，
     只 terminate 父进程会把 ffmpeg 留在后台继续写文件）。
2. **只监听 127.0.0.1**
   它是个本机工具，不是给局域网用的；开着端口又带 Cookie 凭据，暴露出去
   等于把登录态送人。
3. **目标必须像 B 站链接 / BV 号**
   否则它就变成一个「用本机 IP 和带宽去打任意 yt-dlp 站点」的开放代理。

【并发】
最多 2 个任务同时跑（信号量）。B 站有风控，本地起 10 个 yt-dlp 只会更快被拦，
而且会互相抢带宽 —— 表现为「全都慢，没有一个完成」。
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import uuid
import signal
import shutil
import threading
import subprocess
import collections
from urllib.parse import urlsplit, quote

from flask import (
    Flask, request, jsonify, send_from_directory, Response, abort,
)

try:  # 走包内导入；直接以脚本方式跑时退回同目录导入
    from .cookie_manager import CookieManager
    from .downloader import find_ffmpeg
    from .harvest import normalize_target
except ImportError:
    from cookie_manager import CookieManager
    from downloader import find_ffmpeg
    from harvest import normalize_target

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ZJY5_DIR = os.path.dirname(PKG_DIR)                 # zjy5/
DOWNLOADS_DIR = os.path.join(ZJY5_DIR, "downloads")  # 与 CLI 的默认 -o 一致
PYTHON = sys.executable                             # ⚠️ 绝不用裸 "python"
UI_FILE = os.path.join(PKG_DIR, "web_ui.html")

MAX_CONCURRENT = 2          # 见模块 docstring「并发」
LOG_KEEP = 1500             # 每个任务保留的日志行数（内存上限）
DEFAULT_PORT = 8770

# ---------------------------------------------------------------- 目标校验

_BILI_HOST = re.compile(r"(^|\.)(bilibili\.com|b23\.tv|bili2233\.cn)$", re.I)
_BVID = re.compile(r"^BV[0-9A-Za-z]{10}$")
_AVNUM = re.compile(r"^av\d+$", re.I)


def _to_video_url(text: str) -> str | None:
    """把用户填的目标整理成 yt-dlp 能吃的完整 URL。

    - 完整链接：原样返回；
    - 裸 `BV` 号：复用 `harvest.normalize_target` 补成视频链接；
    - 裸 `av` 号：`normalize_target` **只认 BV**（它的 docstring 说 av 会保留，
      实际实现对 av 返回 None），所以这里单独补；
    - 其它：None（调用方负责报错）。
    """
    url = normalize_target(text)
    if url:
        return url
    m = _AVNUM.match((text or "").strip())
    if m:
        return f"https://www.bilibili.com/video/{m.group(0)}"
    return None


def looks_like_bilibili(text: str) -> bool:
    """目标是否像 B 站链接或裸 BV / av 号。

    ⚠️ 这是**输入校验**不是功能限制：不校验的话 `/api/tasks` 就成了
    「拿着本机 Cookie 和 IP 去下载任意站点」的入口。
    """
    s = (text or "").strip()
    if not s:
        return False
    if _BVID.match(s) or _AVNUM.match(s):
        return True
    if not re.match(r"^https?://", s, re.I):
        return False
    try:
        parts = urlsplit(s)
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    return bool(host) and bool(_BILI_HOST.search(host))


# ---------------------------------------------------------------- 任务模型


class Task:
    """一次采集任务 = 一次 CLI 子进程调用。"""

    def __init__(self, mode: str, target: str, argv: list, label: str):
        self.id = uuid.uuid4().hex[:12]
        self.mode = mode
        self.target = target
        self.argv = argv                 # 不含 `-m bilibili_downloader` 的尾部参数
        self.label = label
        self.state = "queued"           # queued / running / done / failed / cancelled
        self.progress = 0.0
        self.created = time.time()
        self.started = None
        self.finished = None
        self.returncode = None
        self.error = None
        self.outputs: list = []         # 日志里认出来的产物路径
        self.log = collections.deque(maxlen=LOG_KEEP)
        self.proc = None

    def to_dict(self, with_log: bool = False) -> dict:
        d = {
            "id": self.id,
            "mode": self.mode,
            "target": self.target,
            "label": self.label,
            "state": self.state,
            "progress": round(self.progress, 1),
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "returncode": self.returncode,
            "error": self.error,
            "outputs": self.outputs,
            "elapsed": round((self.finished or time.time()) - (self.started or self.created), 1),
        }
        if with_log:
            d["log"] = list(self.log)
        return d


_TASKS: "collections.OrderedDict[str, Task]" = collections.OrderedDict()
_TASKS_LOCK = threading.Lock()
_SEM = threading.Semaphore(MAX_CONCURRENT)

# 进度行形如： `[download]  45.2% of  123.45MiB at  1.23MiB/s ETA 00:45`
_PROGRESS_RE = re.compile(r"^\[download\]\s+([\d.]+)%")
_OUTPUT_RE = re.compile(r"✓\s*(?:音频下载完成|下载完成)(?:：|:)\s*(.+)$")
_DEST_RE = re.compile(r"^\[download\]\s+Destination:\s*(.+)$")


def _append(task: Task, line: str) -> None:
    """记一行日志，并顺手从这一行里抠出进度 / 产物。"""
    text = line.rstrip("\r\n")
    if not text:
        return
    task.log.append(text)

    m = _PROGRESS_RE.match(text)
    if m:
        # 多集（频道/搜索）会把进度从头再来一遍；取最大值，进度条不会倒退
        try:
            task.progress = max(task.progress, float(m.group(1)))
        except ValueError:
            pass
        return

    m = _OUTPUT_RE.search(text)
    if m:
        path = m.group(1).strip()
        if path and path not in task.outputs:
            task.outputs.append(path)
        return

    m = _DEST_RE.match(text)
    if m:
        # Destination 只是「将要写到哪」，不算产物（可能后面合并失败）
        return

    # 「开始下一个视频」时把进度归零，多集任务才看得出当前是哪一个
    if text.startswith("正在下载") or text.startswith("正在下载音频"):
        task.progress = 0.0


def _run_task(task: Task) -> None:
    """在子进程里跑 CLI，逐行转发输出。"""
    with _SEM:
        with _TASKS_LOCK:
            if task.state == "cancelled":
                return
            task.state = "running"
            task.started = time.time()

        cmd = [PYTHON, "-m", "bilibili_downloader"] + task.argv
        _append(task, "$ " + " ".join(cmd))

        env = os.environ.copy()
        # 子进程要打印 ✓ / ✗ 这些字符；控制台代码页是 GBK 时会直接
        # UnicodeEncodeError 崩掉（本机实测踩过），必须显式指定编码。
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=ZJY5_DIR,            # `-m bilibili_downloader` 需要包在 sys.path 上
                stdin=subprocess.DEVNULL,  # 兜底：万一某条路径真要 input()，
                                           # 立刻拿到 EOF 报错，而不是无声挂住
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                bufsize=1,
                universal_newlines=True,
            )
        except Exception as e:                       # noqa: BLE001
            with _TASKS_LOCK:
                task.state = "failed"
                task.error = f"{type(e).__name__}: {e}"
                task.finished = time.time()
            _append(task, f"✗ 启动子进程失败：{e}")
            return

        with _TASKS_LOCK:
            task.proc = proc
            if task.state == "cancelled":            # 排队期间被取消
                _kill_tree(proc)
                return

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                _append(task, line)
            proc.wait()
            rc = proc.returncode
        except Exception as e:                       # noqa: BLE001
            rc = -1
            _append(task, f"✗ 读取输出时出错：{e}")

        with _TASKS_LOCK:
            task.proc = None
            task.returncode = rc
            task.finished = time.time()
            if task.state == "cancelled":
                pass
            elif rc == 0:
                task.state = "done"
                task.progress = 100.0
            else:
                task.state = "failed"
                task.error = task.error or f"CLI 退出码 {rc}"
        _append(task, f"—— 进程结束，退出码 {rc}，状态 {task.state} ——")


def _kill_tree(proc: subprocess.Popen) -> None:
    """杀掉整棵进程树（yt-dlp 会 fork ffmpeg）。"""
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=15,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:                                # noqa: BLE001
        try:
            proc.kill()
        except Exception:                            # noqa: BLE001
            pass


# ---------------------------------------------------------------- 参数拼装


def _build_argv(payload: dict) -> tuple:
    """把表单参数翻译成 CLI 参数。返回 (mode, target, argv, label)。"""
    mode = (payload.get("mode") or "video").strip()
    target = (payload.get("target") or "").strip()
    out = payload.get("output") or DOWNLOADS_DIR
    out = os.path.abspath(out)
    if not os.path.isdir(out):
        os.makedirs(out, exist_ok=True)

    quality = payload.get("quality") or "best"
    audio_format = payload.get("audio_format") or "mp3"
    try:
        max_n = int(payload.get("max") or 10)
    except (TypeError, ValueError):
        max_n = 10
    order = (payload.get("order") or "").strip()
    audio_only = bool(payload.get("audio_only"))
    subtitle = bool(payload.get("subtitle"))
    probe_only = bool(payload.get("probe_only"))

    if mode in ("video", "audio", "subtitle"):
        if not looks_like_bilibili(target):
            raise ValueError("请填 B 站视频链接或 BV 号（如 https://www.bilibili.com/video/BV...）")
        # ⚠️ 页面允许填**裸 BV 号**（用户习惯），但 yt-dlp **只认 URL**：
        #    直接把它传给 CLI 会得到 `'BV1...' is not a valid URL`（本机实测）。
        #    这里统一补成完整链接，补不出来就当场报错，别让任务跑起来才失败。
        url = _to_video_url(target)
        if not url:
            raise ValueError(f"认不出这个 BV 号：{target}")
        target = url
        if mode == "audio":
            argv = [target, "-a", "-o", out, "--audio-format", audio_format,
                    "--audio-quality", str(payload.get("audio_quality") or "0")]
            label = f"音频 · {target}"
        elif mode == "subtitle":
            argv = [target, "-o", out, "--subtitle-only",
                    "--format", (payload.get("sub_format") or "text"),
                    # 后台服务没有交互终端：Playwright 会弹浏览器 + input()
                    # 等人工点字幕按钮，不关掉就是把任务永久挂住
                    "--no-playwright"]
            label = f"仅字幕 · {target}"
        else:
            argv = [target, "-o", out, "-q", quality]
            if subtitle:
                argv.append("--subtitle")
                argv.append("--no-playwright")
            if audio_only:      # 视频模式下勾「只要音频」→ 直接走音频路径
                argv = [target, "-a", "-o", out, "--audio-format", audio_format]
            label = f"视频 · {target}" if not audio_only else f"音频 · {target}"
        return mode, target, argv, label

    if mode == "channel":
        if not looks_like_bilibili(target):
            raise ValueError("请填 UP 主空间链接（如 https://space.bilibili.com/123456）")
        argv = ["--channel", target, "-o", out, "--max", str(max_n)]
        if order:
            argv += ["--order", order]
        if audio_only:
            argv += ["-a", "--audio-format", audio_format]
        else:
            argv += ["-q", quality]
        return mode, target, argv, f"UP 空间 · {max_n} 个 · {target}"

    if mode == "search":
        if not target:
            raise ValueError("请填搜索关键词")
        if looks_like_bilibili(target):
            raise ValueError("这是链接 —— 单个链接请用「视频 / 音频」模式")
        argv = ["--search", target, "-o", out, "--max", str(max_n)]
        if order:
            argv += ["--order", order]
        if audio_only:
            argv += ["-a", "--audio-format", audio_format]
        else:
            argv += ["-q", quality]
        return mode, target, argv, f"搜索 · {target} · {max_n} 个"

    if mode == "harvest":
        if not looks_like_bilibili(target) and not (
            target.lower().endswith(".txt") and os.path.isfile(target)
        ):
            raise ValueError("请填 UP 空间/视频链接，或一份 urls.txt 的绝对路径")
        argv = ["--harvest", target, "-o", out, "--max-count", str(max_n)]
        if order:
            argv += ["--order", order]
        if probe_only:
            argv.append("--probe-only")
        return mode, target, argv, f"字幕收割 · {target}"

    raise ValueError(f"不认识的任务类型：{mode}")


# ---------------------------------------------------------------- Flask 应用


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["JSON_AS_ASCII"] = False
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    cookie_mgr = CookieManager()
    cookie_cache = {"checked": None, "ok": None, "message": "尚未检查"}

    def _cookie_check_async() -> None:
        """Cookie 联网校验放到后台：B 站接口慢的时候别把首页卡住。"""
        def work():
            try:
                ok, msg = cookie_mgr.check_cookie_online()
            except Exception as e:                    # noqa: BLE001
                ok, msg = False, f"检查异常：{type(e).__name__}: {e}"
            cookie_cache.update(checked=time.time(), ok=ok, message=msg)
        threading.Thread(target=work, daemon=True).start()

    # ---- 页面 ----
    @app.get("/")
    def index():
        if not os.path.isfile(UI_FILE):
            return Response("缺少 web_ui.html", status=500, mimetype="text/plain")
        with open(UI_FILE, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="text/html; charset=utf-8")

    # ---- 环境自检 ----
    @app.get("/api/health")
    def health():
        try:
            import yt_dlp
            ytdlp = getattr(yt_dlp.version, "__version__", "?")
        except Exception:                             # noqa: BLE001
            ytdlp = None
        ffmpeg = find_ffmpeg()
        if cookie_cache["checked"] is None:
            _cookie_check_async()
        return jsonify({
            "ok": True,
            "python": sys.version.split()[0],
            "ytdlp": ytdlp,
            "ffmpeg": ffmpeg,
            "cookie_file": cookie_mgr.cookie_path,
            "cookie_exists": os.path.exists(cookie_mgr.cookie_path),
            "cookie_ok": cookie_cache["ok"],
            "cookie_message": cookie_cache["message"],
            "downloads": DOWNLOADS_DIR,
            "running": sum(1 for t in _TASKS.values()
                           if t.state in ("queued", "running")),
            "max_concurrent": MAX_CONCURRENT,
        })

    @app.post("/api/cookie-check")
    def cookie_check():
        ok, msg = cookie_mgr.check_cookie_online()
        cookie_cache.update(checked=time.time(), ok=ok, message=msg)
        return jsonify({"ok": ok, "message": msg})

    # ---- 任务 ----
    @app.get("/api/tasks")
    def list_tasks():
        with _TASKS_LOCK:
            items = [t.to_dict() for t in list(_TASKS.values())[-50:]]
        items.reverse()
        return jsonify({"tasks": items})

    @app.get("/api/tasks/<tid>")
    def get_task(tid):
        with _TASKS_LOCK:
            t = _TASKS.get(tid)
            if not t:
                abort(404)
            return jsonify(t.to_dict(with_log=True))

    @app.post("/api/tasks")
    def create_task():
        payload = request.get_json(silent=True) or {}
        try:
            mode, target, argv, label = _build_argv(payload)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

        task = Task(mode, target, argv, label)
        with _TASKS_LOCK:
            _TASKS[task.id] = task
            # 只留最近 200 条，避免长跑后内存无限涨
            while len(_TASKS) > 200:
                _TASKS.popitem(last=False)
        threading.Thread(target=_run_task, args=(task,), daemon=True).start()
        return jsonify({"ok": True, "task": task.to_dict()})

    @app.post("/api/tasks/<tid>/cancel")
    def cancel_task(tid):
        with _TASKS_LOCK:
            t = _TASKS.get(tid)
            if not t:
                abort(404)
            if t.state in ("done", "failed", "cancelled"):
                return jsonify({"ok": False, "error": "任务已结束"}), 409
            t.state = "cancelled"
            proc = t.proc
        _kill_tree(proc)
        return jsonify({"ok": True})

    @app.post("/api/tasks/clear")
    def clear_tasks():
        with _TASKS_LOCK:
            for tid in [k for k, v in _TASKS.items()
                        if v.state in ("done", "failed", "cancelled")]:
                _TASKS.pop(tid, None)
        return jsonify({"ok": True})

    # ---- 产物 ----
    @app.get("/api/files")
    def list_files():
        items = []
        for root, _dirs, names in os.walk(DOWNLOADS_DIR):
            for name in names:
                full = os.path.join(root, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                rel = os.path.relpath(full, DOWNLOADS_DIR).replace("\\", "/")
                items.append({
                    "name": name,
                    "rel": rel,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "kind": _kind_of(name),
                    "url": "/api/files/" + quote(rel),
                })
        items.sort(key=lambda x: x["mtime"], reverse=True)
        return jsonify({"dir": DOWNLOADS_DIR, "files": items[:200]})

    @app.get("/api/files/<path:rel>")
    def get_file(rel):
        target = os.path.abspath(os.path.join(DOWNLOADS_DIR, rel))
        # 目录穿越防护：解析后必须仍在 DOWNLOADS_DIR 里
        if not target.startswith(DOWNLOADS_DIR + os.sep) and target != DOWNLOADS_DIR:
            abort(403)
        if not os.path.isfile(target):
            abort(404)
        return send_from_directory(
            os.path.dirname(target), os.path.basename(target), as_attachment=False,
        )

    return app


def _kind_of(name: str) -> str:
    low = name.lower()
    if low.endswith((".mp4", ".mkv", ".webm", ".flv")):
        return "video"
    if low.endswith((".mp3", ".m4a", ".opus", ".wav", ".flac", ".aac")):
        return "audio"
    if low.endswith((".srt", ".txt", ".jsonl")):
        return "text"
    if low.endswith(".json"):
        return "meta"
    return "other"


# ---------------------------------------------------------------- 入口


def main(argv: list = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="B 站视频/音频采集 · 本地网页服务")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址（默认 127.0.0.1；**不要**改成 0.0.0.0）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"端口（默认 {DEFAULT_PORT}）")
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动开浏览器")
    ns = ap.parse_args(argv)

    app = create_app()
    url = f"http://{ns.host}:{ns.port}"

    print("=" * 58)
    print("  B 站视频/音频采集 · 本地网页服务")
    print(f"  地址 : {url}")
    print(f"  产物 : {DOWNLOADS_DIR}")
    print(f"  并发 : 最多 {MAX_CONCURRENT} 个任务")
    print("  停止 : Ctrl-C")
    print("=" * 58)

    if not ns.no_browser:
        # 延迟一点再开：否则浏览器可能在 Flask 还没绑好端口时就请求
        def open_later():
            time.sleep(1.2)
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:                        # noqa: BLE001
                pass
        threading.Thread(target=open_later, daemon=True).start()

    app.run(host=ns.host, port=ns.port, debug=False, threaded=True,
            use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
