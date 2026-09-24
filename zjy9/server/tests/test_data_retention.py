"""数据保留策略测试：trim_chat_messages / trim_system_logs。

⚠️ 关键：这个测试必须在**临时数据库**上跑。
   trim_chat_messages 是按全库判断的，如果在真实库上跑，会把所有会话
   一起裁到测试用的那个小上限，直接毁掉真实对话记录。

运行：cd server && ./venv/Scripts/python.exe tests/test_data_retention.py
"""
import sys
import tempfile
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER))

import database as db  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  -> ' + str(extra)) if extra else ''}")


# ---- 切到临时库，绝不碰真实数据 ----
_tmpdir = tempfile.TemporaryDirectory()
_orig_db_path = db.DB_PATH
db.DB_PATH = Path(_tmpdir.name) / "retention_test.db"
db.init_db()
print(f"临时库: {db.DB_PATH}")
print(f"（原库路径 {_orig_db_path} 未被触碰）")

try:
    print("\n=== 1. 会话消息裁剪 ===")
    sid_big = db.create_session("t_big", "大会话")
    for i in range(1, 11):
        db.save_message(sid_big, "user" if i % 2 else "assistant", f"消息{i}")

    sid_small = db.create_session("t_small", "小会话")
    db.save_message(sid_small, "user", "只有一条")

    check("大会话原有 10 条", db.get_message_count(sid_big) == 10)
    check("小会话原有 1 条", db.get_message_count(sid_small) == 1)

    deleted = db.trim_chat_messages(max_per_session=4)
    check("裁剪删除了 6 行", deleted == 6, deleted)

    kept = db.get_messages(sid_big, limit=100)
    check("大会话保留 4 条", len(kept) == 4, len(kept))
    check("保留的是【最近】4 条（消息7-10）",
          [m["content"] for m in kept] == ["消息7", "消息8", "消息9", "消息10"],
          [m["content"] for m in kept])

    check("小会话未被误伤", db.get_message_count(sid_small) == 1)

    # message_count 是增量维护的，裁剪后必须与真实条数一致
    sess = db.get_session(sid_big)
    check("message_count 已重算为 4", sess["message_count"] == 4, sess["message_count"])

    print("\n=== 2. 幂等与早退 ===")
    check("再次裁剪无删除", db.trim_chat_messages(max_per_session=4) == 0)
    check("上限为 0 时跳过", db.trim_chat_messages(max_per_session=0) == 0)
    check("上限很大时早退", db.trim_chat_messages(max_per_session=100000) == 0)

    print("\n=== 3. system_logs 裁剪 ===")
    # ⚠️ 不要假设日志表是空的：应用自身在启动时就会写日志
    # （例如数据库迁移完成后会记录 schema 版本）。所以这里断言的是**增量**，
    # 而不是绝对条数 —— 否则上游多打一行日志，这个测试就会莫名其妙地失败。
    logs_before = len(db.get_system_logs(limit=10000))
    for i in range(30):
        db.log_system("INFO", "test", f"日志{i}")
    logs_after = len(db.get_system_logs(limit=10000))
    check("写入 30 条日志", logs_after - logs_before == 30, logs_after - logs_before)
    removed = db.trim_system_logs(max_rows=10)
    check(f"裁剪删除到只剩 10 行（删除 {logs_after - 10} 行）",
          removed == logs_after - 10, removed)
    check("裁剪后剩 10 行", len(db.get_system_logs(limit=100)) == 10,
          len(db.get_system_logs(limit=100)))
    check("再裁无删除", db.trim_system_logs(max_rows=10) == 0)

finally:
    db.close_all_connections()
    db.DB_PATH = _orig_db_path
    _tmpdir.cleanup()
    print("\n[清理] 临时库已删除，原库路径已还原")

print(f"\n=== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ===")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
