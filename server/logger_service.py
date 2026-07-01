"""
日志服务模块
统一的日志记录，同时写入数据库和控制台，并广播给监控面板
"""
import logging
import asyncio
from datetime import datetime
from connection_manager import connection_manager
import database as db


class LoggerService:
    """统一日志服务"""

    def __init__(self):
        # 配置控制台日志
        self.logger = logging.getLogger("elysia_server")
        self.logger.setLevel(logging.DEBUG)

        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S"
        )
        console_handler.setFormatter(console_fmt)
        self.logger.addHandler(console_handler)

        self._loop = None

    def _get_loop(self):
        """获取事件循环"""
        if self._loop is None:
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
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

    def _log(self, level: str, category: str, message: str, detail: str = ""):
        """统一日志记录"""
        # 控制台输出
        log_func = getattr(self.logger, level.lower(), self.logger.info)
        prefix = f"[{category}]" if category else ""
        log_func(f"{prefix} {message}")

        # 写入数据库
        try:
            db.log_system(level.upper(), category, message, detail)
        except Exception:
            pass

        # 广播给监控面板
        log_data = {
            "type": "log",
            "level": level.upper(),
            "category": category,
            "message": message,
            "detail": detail,
            "time": datetime.now().isoformat(),
        }
        self._broadcast(log_data)

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

        # 广播聊天日志给监控面板（FIX #6: 截断内容避免传输过大的消息）
        log_data = {
            "type": "chat",
            "session_id": session_id,
            "role": role,
            "content": content[:200] if len(content) > 200 else content,
            "tokens": tokens,
            "response_ms": response_ms,
            "time": datetime.now().isoformat(),
        }
        self._broadcast(log_data)

    def connection_log(self, event: str, device_id: str, device_name: str,
                       connection_type: str = "wifi", ip_address: str = ""):
        """记录连接事件"""
        self.info("connection", f"{event}: {device_name} ({device_id}) via {connection_type}")

        # 写入连接日志表
        try:
            db.log_connection(device_id, device_name, connection_type,
                              ip_address, event)
        except Exception:
            pass


# 全局日志实例
logger = LoggerService()
