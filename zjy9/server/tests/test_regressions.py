"""zjy9 服务端修复验证脚本（只读为主，写入的测试数据会自行清理）

运行：cd server && ./venv/Scripts/python.exe tests/test_regressions.py
"""
import sys
from pathlib import Path

# 测试脚本位于 server/tests/，需要把 server/ 加进 import 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
import main
import database as db
import config_persistence as cp

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  -> ' + str(extra)) if extra else ''}")


client = TestClient(main.app)

print("\n=== 1. 只读接口可用性 ===")
r = client.get("/api/health/detailed")
check("GET /api/health/detailed 返回 200", r.status_code == 200, r.status_code)
if r.status_code == 200:
    d = r.json()
    check("health 含 uptime_seconds", "uptime_seconds" in d.get("server", {}))
    check("uptime 是服务运行时长(应 < 3600)", d["server"]["uptime_seconds"] < 3600, d["server"]["uptime_seconds"])
    check("connections 统计可用", "active" in d.get("connections", {}))

r = client.get("/api/model/info")
check("GET /api/model/info 返回 200", r.status_code == 200, r.status_code)
if r.status_code == 200:
    check("generation_options 含 num_ctx", "num_ctx" in r.json().get("generation_options", {}),
          r.json().get("generation_options", {}))

r = client.get("/api/messages/search", params={"q": "你好"})
check("GET /api/messages/search 可用（未被 {session_id} 遮蔽）",
      r.status_code == 200 and "results" in r.json(), r.status_code)

r = client.get("/api/session/list")
check("GET /api/session/list 返回 200", r.status_code == 200, r.status_code)
if r.status_code == 200:
    s = r.json()["sessions"]
    check("session/list 有 message_count 字段", (not s) or "message_count" in s[0])

r = client.get("/api/session/stats")
check("GET /api/session/stats 返回 200", r.status_code == 200, r.status_code)

print("\n=== 2. 对话历史顺序（核心 bug）===")
sid = db.create_session("__test_device__", "测试设备", "wifi")
try:
    for i in range(1, 13):
        db.save_message(sid, "user", f"用户消息{i}")
        db.save_message(sid, "assistant", f"助手回复{i}")

    recent = db.get_recent_messages(sid, limit=6)
    contents = [m["content"] for m in recent]
    check("get_recent_messages 返回 6 条", len(recent) == 6, len(recent))
    check("取到的是【最近】6 条而非最早 6 条",
          contents == ["用户消息10", "助手回复10", "用户消息11", "助手回复11", "用户消息12", "助手回复12"],
          contents)
    check("返回顺序为正序（时间递增）", contents[0].endswith("10"), contents[0])

    oldest = db.get_messages(sid, limit=2)
    check("get_messages 仍保持'从头取'语义", [m["content"] for m in oldest] == ["用户消息1", "助手回复1"],
          [m["content"] for m in oldest])

    check("get_message_count 正确", db.get_message_count(sid) == 24, db.get_message_count(sid))

    last = db.get_last_message(sid)
    check("get_last_message 取到最后一条", last and last["content"] == "助手回复12",
          last["content"] if last else None)

    ov = db.get_session_overview(limit=100)
    row = next((x for x in ov if x["session_id"] == sid), None)
    check("get_session_overview 找到测试会话", row is not None)
    if row:
        check("overview 消息数=24（不再被 limit=100 截断）", row["message_count"] == 24, row["message_count"])
        check("overview 预览=最后一条", row["last_message_preview"] == "助手回复12", row["last_message_preview"])

    # 通过 API 验证
    r = client.get(f"/api/messages/{sid}", params={"limit": 4})
    if r.status_code == 200:
        got = [m["content"] for m in r.json()["messages"]]
        check("API /api/messages/{sid} 返回最近 4 条", got == ["用户消息11", "助手回复11", "用户消息12", "助手回复12"], got)

    print("\n=== 3. 记忆库 list_all / 导出 ===")
    mm = main.memory_manager
    # 注意：add_fact 会按内容做 md5 去重，重复添加同一条不会增加计数，
    # 所以这里用带时间戳的唯一内容，保证"新增后计数 +1"的断言有效。
    import time as _t
    unique_fact = f"__测试事实__：舰长喜欢喝咖啡 {_t.time_ns()}"
    before = len(mm.list_all("facts"))
    mm.add_fact(unique_fact, source="test", session_id=sid)
    after = mm.list_all("facts")
    check("list_all 能列出条目（不再因空查询返回空）", len(after) == before + 1, f"{before} -> {len(after)}")
    check("新增事实可被检索到", any("咖啡" in x["content"] for x in after))

    r = client.get("/api/memory/export")
    if r.status_code == 200:
        facts = r.json()["collections"].get("facts", {})
        check("GET /api/memory/export 不再返回空", facts.get("count", 0) > 0, facts.get("count"))

    r = client.post("/api/memory/cleanup")
    check("POST /api/memory/cleanup 可用", r.status_code == 200, r.status_code)

    print("\n=== 4. 运行时配置生效 ===")
    cp.save_config({"rate_limit_per_minute": 999})
    check("runtime() 读到刚写入的值", main.runtime("rate_limit_per_minute", 120) == 999,
          main.runtime("rate_limit_per_minute", 120))

    cp.save_config({"api_token": "__test_token_abc__"})
    check("api_token 运行时生效（鉴权不再读死常量）", main._current_api_token() == "__test_token_abc__")

    # 开了 token 之后，受保护接口应 401，静态/健康检查应放行
    r = client.get("/api/config")
    check("开启 token 后 /api/config 需认证（401）", r.status_code == 401, r.status_code)
    r = client.get("/api/memory/stats")
    check("开启 token 后 /api/memory/stats 需认证（401）", r.status_code == 401, r.status_code)
    r = client.get("/health")
    check("开启 token 后 /health 仍放行", r.status_code == 200, r.status_code)
    r = client.get("/api/config", headers={"Authorization": "Bearer __test_token_abc__"})
    check("带正确 token 可访问 /api/config", r.status_code == 200, r.status_code)

    print("\n=== 5. 聊天链路关键修复（无需模型）===")
    # 回归 1：main 里的 runtime 必须是 config.runtime 函数，不能被 load_config()
    # 的返回值遮蔽 —— 曾导致 get_ai_stream() 里 runtime("...") 报
    # 'dict' object is not callable，进而 /api/chat 全部 500。
    check("main.runtime 仍可调用（未被局部变量遮蔽）", callable(main.runtime))
    check("main.runtime(...) 可正常取值",
          main.runtime("rate_limit_per_minute", 1) is not None)

    # 回归 2：_emotion_consistency 的入参类型 —— check_response_consistency()
    # 只收 dict，曾把 EmotionResult 对象/字符串直接传进去导致 TypeError。
    from types import SimpleNamespace
    fake_emo = SimpleNamespace(valence=0.5, arousal=0.3)
    re_, co = main._emotion_consistency(
        fake_emo, "这是一句足够长的测试回复文本，用于触发情绪分析。")
    check("_emotion_consistency 返回 (dict, dict)",
          isinstance(re_, dict) and isinstance(co, dict))
    check("一致性结果含 consistent 字段", "consistent" in co)
    check("过短回复返回 (None, None)",
          main._emotion_consistency(fake_emo, "短") == (None, None))

finally:
    # 清理测试数据
    db.delete_session(sid)
    mm = main.memory_manager
    # 删掉测试写入的记忆条目（后端没有单条删除接口，用重建集合实现）
    for coll in ["facts", "conversations", "summaries"]:
        items = mm.list_all(coll)
        keep = [x for x in items if "__测试" not in x.get("content", "")
                and "咖啡" not in x.get("content", "")]
        if len(keep) != len(items):
            mm.backend.clear(coll)
            for x in keep:
                mm.backend.add_document(coll, x["id"], x["content"], x.get("metadata", {}))
    mm.flush()
    cp.save_config({"rate_limit_per_minute": 120, "api_token": ""})
    print("\n[清理] 测试会话/记忆已删除，运行时配置已还原")

print(f"\n=== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ===")
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("  -", f)
