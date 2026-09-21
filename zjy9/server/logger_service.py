"""
日志服务模块
统一的日志记录，同时写入数据库和控制台，并广播给监控面板

v2 变更：
- 数据库写入改为后台线程 + 队列。原实现在 async 请求路径里同步
  `INSERT` + `commit`，每条日志都会阻塞事件循环，并发一上来延迟急剧劣化。
- 增加日志表保留策略，防止 system_logs 无限增长。
- 广播改为在后台线程里用 run_coroutine_threadsafe（跨线程的正确用法）。
"""
import logging
import asyncio
import queue
import threading
from datetime import datetime
from connection_manager import connection_manager
import database as db


class LoggerService:
    """统一日志服务"""

    # 内存队列上限，超过则丢弃（日志不能反过来把服务拖垮）
    _QUEUE_MAXSIZE = 5000
    # system_logs 表保留的最大行数
    _MAX_LOG_ROWS = 20000
    # 每隔多少条日志检查一次是否需要清理
    _CLEANUP_EVERY = 500

    def __init__(self):
        # 配置控制台日志
        self.logger = logging.getLogger("elysia_server")
        self.logger.setLevel(logging.DEBUG)
        # 避免重复添加 handler（模块被重新导入时会出现重复输出）
        if not self.logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            console_fmt = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S"
            )
            console_handler.setFormatter(console_fmt)
            self.logger.addHandler(console_handler)

        self._loop = None
        self._queue: queue.Queue = queue.Queue(maxsize=self._QUEUE_MAXSIZE)
        self._written = 0
        self._stopping = False
        self._worker = threading.Thread(
            target=self._drain_loop, name="log-writer", daemon=True
        )
        self._worker.start()

    # ===== 事件循环 =====

    def set_loop(self, loop):
        """由应用启动时注入事件循环（比在日志里猜更可靠）"""
        self._loop = loop

    def _get_loop(self):
        """获取事件循环"""
        if self._loop is not None:
            return self._loop
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = None
        return self._loop

    def _broadcast(self, log_data: dict):
        """异步广播日志（线程安全）"""
        try:
            loop = self._get_loop()
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    connection_manager.broadcast_log(log_data),
                    loop
                )
        except Exception:
            pass  # 广播失败不影响主流程

    # ===== 后台写入线程 =====

    def _drain_loop(self):
        """后台线程：从队列取出日志写入数据库并广播"""
        while True:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                if self._stopping:
                    return
                continue

            if item is None:  # 停机哨兵
                return

            kind, payload = item
            try:
                if kind == "system":
                    db.log_system(*payload)
                elif kind == "connection":
                    db.log_connection(*payload)
                elif kind == "broadcast":
                    self._broadcast(payload)
            except Exception:
                pass  # 日志写库/广播失败不能影响主流程

            self._written += 1
            if self._written % self._CLEANUP_EVERY == 0:
                try:
                    db.trim_system_logs(self._MAX_LOG_ROWS)
                except Exception:
                    pass

    def _enqueue(self, kind: str, payload):
        try:
            self._queue.put_nowait((kind, payload))
        except queue.Full:
            pass  # 队列满则丢弃，避免反压阻塞业务

    def shutdown(self):
        """停止后台线程并尽量把队列写完（优雅关闭时调用）"""
        self._stopping = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker.is_alive():
            self._worker.join(timeout=3.0)

    # ===== 记录接口 =====

    def _log(self, level: str, category: str, message: str, detail: str = ""):
        """统一日志记录"""
        # 控制台输出（同步，开销可忽略）
        log_func = getattr(self.logger, level.lower(), self.logger.info)
        prefix = f"[{category}]" if category else ""
        log_func(f"{prefix} {message}")

        # 写数据库：交给后台线程，不阻塞调用方
        self._enqueue("system", (level.upper(), category, message, detail))

        # 广播给监控面板
        self._enqueue("broadcast", {
            "type": "log",
            "level": level.upper(),
            "category": category,
            "message": message,
            "detail": detail,
            "time": datetime.now().isoformat(),
        })

    def info(self, category: str, message: str, detail: str = ""):
        """记录 INFO 级别日志"""
        self._log("INFO", category, message, detail)

    def warning(self, category: str, message: str, detail: str = ""):
        """记录 WARNING 级别日志"""
        self._log("WARNING", category, message, detail)

    def error(self, category: str, message: str, detail: str = ""):
        """记录 ERROR 级别日志"""
        self._log("ERROR", category, message, detail)

    def debug(self, category: str, message: str, detail: str = ""):
        """记录 DEBUG 级别日志"""
        self._log("DEBUG", category, message, detail)

    def chat_log(self, session_id: str, role: str, content: str,
                 tokens: int = 0, response_ms: int = 0):
        """记录聊天消息日志"""
        preview = content[:50] + "..." if len(content) > 50 else content
        self.info("chat", f"[{role}] {preview}")

        # 广播聊天日志给监控面板（截断内容避免传输过大的消息）
        self._enqueue("broadcast", {
            "type": "chat",
            "session_id": session_id,
            "role": role,
            "content": content[:200] if len(content) > 200 else content,
            "tokens": tokens,
            "response_ms": response_ms,
            "time": datetime.now().isoformat(),
        })

    def connection_log(self, event: str, device_id: str, device_name: str,
                       connection_type: str = "wifi", ip_address: str = ""):
        """记录连接事件"""
        self.info("connection", f"{event}: {device_name} ({device_id}) via {connection_type}")

        # 写连接日志表（同样交给后台线程）
        self._enqueue("connection", (device_id, device_name, connection_type,
                                     ip_address, event))


# 全局日志实例
logger = LoggerService()
