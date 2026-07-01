"""
数据库管理模块 - SQLite
管理所有表的创建、查询、插入操作
使用单连接复用，避免频繁创建/销毁连接的 I/O 开销
"""
import sqlite3
import uuid
import threading
from datetime import datetime
from contextlib import contextmanager
from config import DB_PATH

# 线程本地存储：每个线程使用独立连接，避免 SQLite 多线程并发冲突
_local = threading.local()

# 用于保护写入操作的锁
_write_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    """获取当前线程的数据库连接（懒初始化 + 复用）"""
    if not hasattr(_local, 'conn') or _local.conn is None:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return _local.conn


def get_connection():
    """获取数据库连接（兼容旧接口的上下文管理器）"""
    return _get_conn()


def _execute_with_lock(sql: str, params=()):
    """带写入锁的 SQL 执行（供写操作使用）"""
    with _write_lock:
        conn = _get_conn()
        conn.execute(sql, params)
        conn.commit()


def close_connection():
    """关闭当前线程的数据库连接（优雅关闭时调用）"""
    if hasattr(_local, 'conn') and _local.conn is not None:
        _local.conn.close()
        _local.conn = None


def init_db():
    """初始化数据库，创建所有表"""
    conn = _get_conn()
    cursor = conn.cursor()
    with _write_lock:
        # 聊天会话表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                device_name TEXT DEFAULT '',
                device_type TEXT DEFAULT 'wifi',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_active DATETIME DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            )
        """)

        # 聊天消息表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tokens_used INTEGER DEFAULT 0,
                response_time_ms INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id)
            )
        """)

        # 连接日志表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS connection_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                device_name TEXT DEFAULT '',
                connection_type TEXT DEFAULT 'wifi',
                ip_address TEXT DEFAULT '',
                event TEXT NOT NULL,
                detail TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 系统日志表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                category TEXT DEFAULT 'system',
                message TEXT NOT NULL,
                detail TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 添加索引加速查询
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_role ON chat_messages(role)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON chat_sessions(status, last_active)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON system_logs(level, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conn_logs_event ON connection_logs(event, created_at)")

        conn.commit()


# ===== 会话管理 =====

def create_session(device_id: str, device_name: str = "", device_type: str = "wifi") -> str:
    """创建新的聊天会话"""
    session_id = str(uuid.uuid4())
    _execute_with_lock(
        "INSERT INTO chat_sessions (id, device_id, device_name, device_type) VALUES (?, ?, ?, ?)",
        (session_id, device_id, device_name, device_type)
    )
    return session_id


def get_session(session_id: str) -> dict | None:
    """获取会话信息"""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def update_session_active(session_id: str):
    """更新会话最后活跃时间"""
    _execute_with_lock(
        "UPDATE chat_sessions SET last_active = CURRENT_TIMESTAMP WHERE id = ?",
        (session_id,)
    )


def get_recent_sessions(limit: int = 20) -> list:
    """获取最近的会话列表"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM chat_sessions ORDER BY last_active DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def close_session(session_id: str):
    """关闭会话"""
    _execute_with_lock(
        "UPDATE chat_sessions SET status = 'closed' WHERE id = ?",
        (session_id,)
    )


def delete_session(session_id: str) -> bool:
    """删除会话及其所有消息"""
    with _write_lock:
        conn = _get_conn()
        # 先删除消息
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        # 再删除会话
        cursor = conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_message(message_id: str) -> bool:
    """删除单条消息"""
    with _write_lock:
        conn = _get_conn()
        # 获取消息所属会话，更新消息计数
        row = conn.execute("SELECT chat_sessions.id FROM chat_messages JOIN chat_sessions ON chat_messages.session_id = chat_sessions.id WHERE chat_messages.id = ?", (message_id,)).fetchone()
        if not row:
            return False
        session_id = row[0]
        conn.execute("DELETE FROM chat_messages WHERE id = ?", (message_id,))
        conn.execute(
            "UPDATE chat_sessions SET message_count = MAX(message_count - 1, 0) WHERE id = ?",
            (session_id,)
        )
        conn.commit()
        return True


def search_messages(query: str, session_id: str = None, limit: int = 50) -> list:
    """搜索消息内容"""
    conn = _get_conn()
    if session_id:
        rows = conn.execute(
            """SELECT * FROM chat_messages 
               WHERE session_id = ? AND content LIKE ? 
               ORDER BY created_at DESC LIMIT ?""",
            (session_id, f"%{query}%", limit)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM chat_messages 
               WHERE content LIKE ? 
               ORDER BY created_at DESC LIMIT ?""",
            (f"%{query}%", limit)
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_sessions(limit: int = 100, status: str = None) -> list:
    """获取所有会话（支持状态过滤）"""
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM chat_sessions WHERE status = ? ORDER BY last_active DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM chat_sessions ORDER BY last_active DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_old_messages(days: int = 30) -> list:
    """获取超过指定天数的消息（用于记忆过期清理）"""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT * FROM chat_messages 
           WHERE created_at < datetime('now', ? || ' days')
           ORDER BY created_at ASC""",
        (f"-{days}",)
    ).fetchall()
    return [dict(r) for r in rows]


# ===== 消息管理 =====

def save_message(session_id: str, role: str, content: str,
                 tokens_used: int = 0, response_time_ms: int = 0) -> str:
    """保存聊天消息"""
    msg_id = str(uuid.uuid4())
    with _write_lock:
        conn = _get_conn()
        conn.execute(
            """INSERT INTO chat_messages 
               (id, session_id, role, content, tokens_used, response_time_ms) 
               VALUES (?, ?, ?, ?, ?, ?)""",
            (msg_id, session_id, role, content, tokens_used, response_time_ms)
        )
        conn.execute(
            """UPDATE chat_sessions 
               SET message_count = message_count + 1, last_active = CURRENT_TIMESTAMP 
               WHERE id = ?""",
            (session_id,)
        )
        conn.commit()
    return msg_id


def get_messages(session_id: str, limit: int = 100) -> list:
    """获取会话的消息历史"""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT * FROM chat_messages 
           WHERE session_id = ? 
           ORDER BY created_at ASC 
           LIMIT ?""",
        (session_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


# ===== 连接日志 =====

def log_connection(device_id: str, device_name: str, connection_type: str,
                   ip_address: str, event: str, detail: str = ""):
    """记录连接事件"""
    _execute_with_lock(
        """INSERT INTO connection_logs 
           (device_id, device_name, connection_type, ip_address, event, detail) 
           VALUES (?, ?, ?, ?, ?, ?)""",
        (device_id, device_name, connection_type, ip_address, event, detail)
    )


def get_connection_logs(limit: int = 100) -> list:
    """获取连接日志"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM connection_logs ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ===== 系统日志 =====

def log_system(level: str, category: str, message: str, detail: str = ""):
    """记录系统日志"""
    _execute_with_lock(
        """INSERT INTO system_logs (level, category, message, detail) 
           VALUES (?, ?, ?, ?)""",
        (level, category, message, detail)
    )


def get_system_logs(limit: int = 200, level: str = None) -> list:
    """获取系统日志"""
    conn = _get_conn()
    if level:
        rows = conn.execute(
            "SELECT * FROM system_logs WHERE level = ? ORDER BY created_at DESC LIMIT ?",
            (level, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM system_logs ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ===== 统计 =====

def get_stats() -> dict:
    """获取统计数据"""
    conn = _get_conn()
    total_sessions = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    total_messages = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
    active_sessions = conn.execute(
        "SELECT COUNT(*) FROM chat_sessions WHERE status = 'active'"
    ).fetchone()[0]
    today_messages = conn.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE date(created_at) = date('now')"
    ).fetchone()[0]

    return {
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "active_sessions": active_sessions,
        "today_messages": today_messages,
    }
