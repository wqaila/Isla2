"""
数据库管理模块 - SQLite
管理所有表的创建、查询、插入操作
使用单连接复用，避免频繁创建/销毁连接的 I/O 开销
"""
import sqlite3
import uuid
import threading
from datetime import datetime
from config import DB_PATH

# 线程本地存储：每个线程使用独立连接，避免 SQLite 多线程并发冲突
_local = threading.local()

# 用于保护写入操作的锁
_write_lock = threading.Lock()

# 记录所有已创建的连接，供优雅关闭时统一释放（避免线程退出时连接泄漏）
_all_conns: list[sqlite3.Connection] = []
_all_conns_lock = threading.Lock()

# 写入忙等超时（毫秒）：WAL 模式下多线程写入时避免直接抛 "database is locked"
_BUSY_TIMEOUT_MS = 5000


# ===== Schema 版本与迁移 =====
#
# 之前完全没有迁移机制：表结构是"一次建好、永不改变"的假设。一旦要加字段，
# 老数据库不会自动升级（要么启动报错，要么静默用错结构），而这个库已经跑了
# 几个月、里面是真实对话记录。
#
# 现在用 SQLite 内置的 PRAGMA user_version 记录版本号，按版本逐级升级。
#
# 以后要改表结构时：
#   1. 把 SCHEMA_VERSION 加 1
#   2. 在 _MIGRATIONS 里补一个该版本的迁移函数
#   3. 同时把新列写进上面的 CREATE TABLE（让全新库直接就是最新结构）
# 迁移函数必须是**幂等**的——全新库走 CREATE TABLE 时已经带上新列了，
# 迁移函数再执行一次不能出错。

SCHEMA_VERSION = 1


def _table_columns(cursor, table: str) -> set:
    """返回某张表已有的列名集合（用于幂等判断）"""
    return {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}


def _migrate_v1(cursor, conn):
    """v1：给 chat_sessions 增加 title 字段（会话标题）。"""
    if "title" not in _table_columns(cursor, "chat_sessions"):
        cursor.execute("ALTER TABLE chat_sessions ADD COLUMN title TEXT DEFAULT ''")


_MIGRATIONS = {
    1: _migrate_v1,
}


def _apply_migrations(cursor, conn) -> int:
    """把数据库从当前版本逐级升级到 SCHEMA_VERSION，返回升级后的版本号"""
    current = cursor.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return current
    for version in range(current + 1, SCHEMA_VERSION + 1):
        fn = _MIGRATIONS.get(version)
        if fn is not None:
            fn(cursor, conn)
        # PRAGMA 不支持参数绑定；version 来自 range()，不存在注入风险
        cursor.execute(f"PRAGMA user_version={version}")
    conn.commit()
    return SCHEMA_VERSION


# 连接"代次"：close_all_connections() 会 +1。
# 为什么需要：threading.local 只能改**本线程**的属性。close_all_connections()
# 在 A 线程关掉了 B 线程的连接后，没法清掉 B 线程的缓存引用，B 线程之后会
# 一直拿到一个已关闭的连接，永久报 "Cannot operate on a closed database"。
# 用代次号让各线程自己发现"我缓存的连接已经作废了"，无需昂贵的探活查询。
_conn_generation = 0


def _get_conn() -> sqlite3.Connection:
    """获取当前线程的数据库连接（懒初始化 + 复用）"""
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "gen", -1) != _conn_generation:
        conn = None                     # 全局已换代，本线程缓存的连接作废
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
        _local.gen = _conn_generation
        with _all_conns_lock:
            _all_conns.append(conn)
    return conn


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
    """关闭当前线程的数据库连接"""
    if hasattr(_local, 'conn') and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        with _all_conns_lock:
            if _local.conn in _all_conns:
                _all_conns.remove(_local.conn)
        _local.conn = None


def close_all_connections():
    """关闭所有线程创建的连接（优雅关闭时调用，防止连接泄漏）"""
    global _conn_generation
    with _all_conns_lock:
        for conn in list(_all_conns):
            try:
                conn.close()
            except Exception:
                pass
        _all_conns.clear()
    # 换代：其它线程下次取连接时会发现自己的缓存已作废，自动重建
    _conn_generation += 1
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
                status TEXT DEFAULT 'active',
                title TEXT DEFAULT ''
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

        # 结构升级：老库在这里补齐后来新增的字段（全新库因 CREATE TABLE 已是最新，迁移为空操作）
        applied = _apply_migrations(cursor, conn)
        print(f"[DB] schema 版本: {applied}")

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


def set_session_title(session_id: str, title: str) -> bool:
    """设置会话标题"""
    _execute_with_lock(
        "UPDATE chat_sessions SET title = ? WHERE id = ?",
        ((title or "").strip()[:100], session_id)
    )
    return True


def auto_title_session(session_id: str, text: str) -> None:
    """会话还没有标题时，用首条用户消息自动起一个。

    只在 title 为空时写入，所以后续消息不会覆盖。失败不影响主流程。
    """
    snippet = " ".join((text or "").split())
    if not snippet:
        return
    snippet = snippet[:20] + ("…" if len(snippet) > 20 else "")
    try:
        with _write_lock:
            conn = _get_conn()
            conn.execute(
                "UPDATE chat_sessions SET title = ? "
                "WHERE id = ? AND (title IS NULL OR title = '')",
                (snippet, session_id)
            )
            conn.commit()
    except Exception:
        pass


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


def get_session_overview(limit: int = 20) -> list:
    """一次性取回会话列表 + 消息数 + 最后一条消息预览。

    用两条聚合查询代替「每个会话再查一次消息」的 N+1 写法。
    """
    conn = _get_conn()
    sessions = conn.execute(
        "SELECT * FROM chat_sessions ORDER BY last_active DESC LIMIT ?",
        (limit,)
    ).fetchall()
    if not sessions:
        return []

    session_ids = [s["id"] for s in sessions]
    placeholders = ",".join("?" * len(session_ids))

    # 每条会话的真实消息数（用 COUNT 聚合，而不是把消息捞出来数长度）
    counts = dict(conn.execute(
        f"""SELECT session_id, COUNT(*) FROM chat_messages
            WHERE session_id IN ({placeholders})
            GROUP BY session_id""",
        session_ids
    ).fetchall())

    # 每条会话的最后一条消息（用相关子查询一次取回）
    last_rows = conn.execute(
        f"""SELECT m.session_id, m.content
            FROM chat_messages m
            JOIN (
                SELECT session_id, MAX(rowid) AS max_rowid
                FROM chat_messages
                WHERE session_id IN ({placeholders})
                GROUP BY session_id
            ) t ON m.session_id = t.session_id AND m.rowid = t.max_rowid""",
        session_ids
    ).fetchall()
    last_msg = {r["session_id"]: r["content"] for r in last_rows}

    result = []
    for s in sessions:
        sid = s["id"]
        preview = last_msg.get(sid, "") or ""
        result.append({
            "session_id": sid,
            "device_id": s["device_id"],
            "device_name": s["device_name"],
            "created_at": s["created_at"],
            "last_active": s["last_active"],
            "status": s["status"],
            "message_count": counts.get(sid, 0),
            "last_message_preview": preview[:50],
        })
    return result


def get_message_total() -> int:
    """消息总数（用于统计，避免逐会话累加）"""
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()
    return row[0] if row else 0


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
    """获取会话最早的消息（按时间正序，最多 limit 条）

    注意：如果你想要的是"最近 N 条对话上下文"，请用 get_recent_messages()。
    本函数保留"从头取"的语义，用于按顺序翻页/导出场景。
    """
    conn = _get_conn()
    rows = conn.execute(
        """SELECT * FROM chat_messages 
           WHERE session_id = ? 
           ORDER BY created_at ASC, rowid ASC 
           LIMIT ?""",
        (session_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]


def get_messages_range(session_id: str, offset: int, limit: int) -> list:
    """按时间正序取会话中第 [offset, offset+limit) 条消息。

    给"会话中期摘要"用：只需要总结「最近窗口之外」的旧消息 ——
    既不能把整个会话重新读一遍，也不能把最近的消息重复总结。
    """
    if limit <= 0:
        return []
    conn = _get_conn()
    rows = conn.execute(
        """SELECT * FROM chat_messages
           WHERE session_id = ?
           ORDER BY created_at ASC, rowid ASC
           LIMIT ? OFFSET ?""",
        (session_id, limit, max(0, offset))
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_messages(session_id: str, limit: int = 10) -> list:
    """获取会话**最近** limit 条消息，并按时间正序返回。

    用于构建对话上下文：必须是"最近的"而不是"最早的"。
    排序用 (created_at, rowid) 双键，因为 created_at 只有秒级精度，
    同一秒内的多条消息靠 rowid（自增插入序）才能保证稳定顺序。
    """
    conn = _get_conn()
    # 先按"倒序"取最近 limit 条，再在 Python 里反转成正序。
    # 不能写成 `SELECT * FROM (子查询) ORDER BY rowid` —— 子查询用 SELECT *
    # 输出的是表字段，外层看不到 rowid，会报 "no such column: rowid"。
    rows = conn.execute(
        """SELECT * FROM chat_messages
           WHERE session_id = ?
           ORDER BY created_at DESC, rowid DESC
           LIMIT ?""",
        (session_id, limit)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_last_message(session_id: str) -> dict | None:
    """获取会话的最后一条消息（用于列表页预览）"""
    conn = _get_conn()
    row = conn.execute(
        """SELECT * FROM chat_messages
           WHERE session_id = ?
           ORDER BY created_at DESC, rowid DESC
           LIMIT 1""",
        (session_id,)
    ).fetchone()
    return dict(row) if row else None


def get_message_count(session_id: str) -> int:
    """统计会话的消息数（走 COUNT，避免把消息全查出来）"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?",
        (session_id,)
    ).fetchone()
    return row[0] if row else 0


# ===== 导出与备份 =====

def _session_messages(conn, session_id: str) -> list:
    rows = conn.execute(
        """SELECT role, content, tokens_used, response_time_ms, created_at
           FROM chat_messages WHERE session_id = ?
           ORDER BY created_at ASC, rowid ASC""",
        (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def export_session(session_id: str) -> dict | None:
    """导出单个会话（元信息 + 全部消息），不存在返回 None。

    注意：不能用 get_messages(session_id, limit=0) —— 那是 `LIMIT 0`，
    返回的是空列表。这里直接不带 LIMIT 查全量。
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        return None
    msgs = _session_messages(conn, session_id)
    return {"session": dict(row), "message_count": len(msgs), "messages": msgs}


def export_all_sessions() -> dict:
    """导出全部会话及其消息（完整对话留档，用于备份 / 迁移）。"""
    conn = _get_conn()
    sessions = conn.execute(
        "SELECT * FROM chat_sessions ORDER BY last_active DESC"
    ).fetchall()
    out = []
    for s in sessions:
        msgs = _session_messages(conn, s["id"])
        out.append({
            "session": dict(s),
            "message_count": len(msgs),
            "messages": msgs,
        })
    return {
        "session_count": len(out),
        "message_count": sum(x["message_count"] for x in out),
        "sessions": out,
    }


# 备份目录（与数据库同级的 backups/，不入库）
BACKUP_DIR = DB_PATH.parent / "backups"


def _prune_backups(keep: int) -> int:
    """只保留最近 keep 份备份，返回删除数量"""
    if keep <= 0 or not BACKUP_DIR.exists():
        return 0
    files = sorted(BACKUP_DIR.glob("elysia_server-*.db"))
    removed = 0
    for f in (files[:-keep] if len(files) > keep else []):
        try:
            f.unlink()
            removed += 1
        except Exception:
            pass
    return removed


def backup_database(keep: int = 5) -> dict:
    """用 VACUUM INTO 做一次一致性快照，并只保留最近 keep 份。

    为什么不用直接复制文件：WAL 模式下 `.db` 与 `-wal` 是分离的，直接 copy
    很可能拿到一个不含最新写入的半成品。VACUUM INTO 会输出一个自洽的完整
    副本，而且**不需要停服**。
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"elysia_server-{stamp}.db"
    conn = _get_conn()
    with _write_lock:
        if target.exists():
            target.unlink()          # VACUUM INTO 遇到已存在的文件会报错
        conn.execute("VACUUM INTO ?", (str(target),))

    removed = _prune_backups(keep)
    return {
        "file": target.name,
        "size_bytes": target.stat().st_size,
        "removed_old": removed,
        "kept": len(list_backups()),
    }


def list_backups() -> list:
    """列出已有备份（按时间倒序）"""
    if not BACKUP_DIR.exists():
        return []
    files = sorted(BACKUP_DIR.glob("elysia_server-*.db"), reverse=True)
    return [{
        "file": f.name,
        "size_bytes": f.stat().st_size,
        "created_at": datetime.fromtimestamp(
            f.stat().st_mtime).isoformat(timespec="seconds"),
    } for f in files]


def latest_backup_age_seconds() -> float | None:
    """最近一次备份距今多少秒；一份备份都没有时返回 None。

    给"自动备份"用：只有手动按钮等于没有，没人会记得按。
    """
    if not BACKUP_DIR.exists():
        return None
    files = list(BACKUP_DIR.glob("elysia_server-*.db"))
    if not files:
        return None
    newest = max(f.stat().st_mtime for f in files)
    return max(0.0, datetime.now().timestamp() - newest)


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


def trim_system_logs(max_rows: int = 20000) -> int:
    """把 system_logs 裁剪到 max_rows 行以内（保留最新的），返回删除行数。

    原实现没有任何清理策略，日志表会无限增长，最终拖慢整个库。
    """
    conn = _get_conn()
    with _write_lock:
        total = conn.execute("SELECT COUNT(*) FROM system_logs").fetchone()[0]
        if total <= max_rows:
            return 0
        excess = total - max_rows
        cursor = conn.execute(
            """DELETE FROM system_logs WHERE id IN (
                   SELECT id FROM system_logs ORDER BY created_at ASC, id ASC LIMIT ?
               )""",
            (excess,)
        )
        conn.commit()
        return cursor.rowcount


def trim_chat_messages(max_per_session: int = 500) -> int:
    """每个会话只保留最近 max_per_session 条消息，返回删除的行数。

    system_logs 早有 trim_system_logs，但 chat_messages 一直没有裁剪策略 ——
    对话会无限增长。对一个"陪伴型"应用来说，这是最容易忽略又迟早要还的债。

    注意：message_count 是**增量维护**的（保存 +1 / 删除单条 -1），所以裁剪完
    必须整体重算一次，否则会话列表上的消息数会和实际对不上。
    """
    if max_per_session <= 0:
        return 0
    conn = _get_conn()
    with _write_lock:
        # 便宜的早退：总量都没超过单会话上限，就不可能超限
        total = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        if total <= max_per_session:
            return 0

        cursor = conn.execute(
            """DELETE FROM chat_messages WHERE rowid IN (
                   SELECT rowid FROM (
                       SELECT rowid,
                              ROW_NUMBER() OVER (
                                  PARTITION BY session_id
                                  ORDER BY created_at DESC, rowid DESC
                              ) AS rn
                       FROM chat_messages
                   ) WHERE rn > ?
               )""",
            (max_per_session,)
        )
        deleted = cursor.rowcount
        if deleted:
            conn.execute(
                """UPDATE chat_sessions SET message_count = (
                       SELECT COUNT(*) FROM chat_messages
                       WHERE session_id = chat_sessions.id
                   )"""
            )
            conn.commit()
        return deleted


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
