"""端到端对话冒烟测试：真实调用本机 Ollama，验证完整链路。

覆盖（此前完全没测过的路径）：
  认证中间件 -> 会话创建 -> 上下文组装(人设/记忆/时间/情感)
  -> Ollama 真实流式 -> 落库 -> 情绪分析/一致性检查

前置：本机 Ollama 正在运行（否则自动跳过，不判失败）。
测试数据在 lifespan 关闭前自行清理。
运行：cd server && ./venv/Scripts/python.exe tests/test_chat_e2e.py
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER))

from fastapi.testclient import TestClient
import main
import database as db

PASS, FAIL = [], []

# 测试专用话术（清理记忆库时按这两句精确匹配，保持单一来源）
MSG_NONSTREAM = "你好，请用一句话自我介绍"
MSG_STREAM = "再说一句很短的话"


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  -> ' + str(extra)) if extra else ''}")


def ollama_alive() -> bool:
    """不依赖事件循环地探测本机 Ollama（避免和 TestClient 的 loop 打架）"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            json.loads(r.read())
        return True
    except Exception:
        return False


if not ollama_alive():
    print("SKIP: 本机 Ollama 未运行，跳过端到端对话测试（不算失败）")
    sys.exit(0)

created_sessions = []

# 注意：必须用 with 触发 lifespan；清理也要放在 with 内部 ——
# 一旦退出 with，lifespan 会 close_all_connections，之后再操作库会报
# "Cannot operate on a closed database"。
with TestClient(main.app) as client:
    # ---------- 1. 非流式 ----------
    print("\n=== 1. 非流式对话（真实模型）===")
    t0 = time.time()
    r = client.post("/api/chat", json={
        "message": MSG_NONSTREAM,
        "device_id": "smoke_test_dev",
        "device_name": "冒烟测试",
        "stream": False,
    })
    dt = time.time() - t0
    check("POST /api/chat -> 200", r.status_code == 200, r.status_code)

    sid = None
    if r.status_code == 200:
        d = r.json()
        sid = d.get("session_id")
        created_sessions.append(sid)
        content = d.get("content", "") or ""
        check("返回非空回复", bool(content.strip()), f"{len(content)} 字符 / {dt:.1f}s")
        check("返回 session_id", bool(sid), sid)
        check("回复不含错误标记", "[错误]" not in content, content[:60].replace("\n", " "))
        # 回归：模型曾把记忆上下文的 [标签] 照抄进回复（"[历史对话]"）
        _labels = ("[历史对话]", "[记忆]", "[对话摘要]", "[用户基本信息]", "[性格特点]")
        check("回复未回声提示词标签",
              not any(t in content for t in _labels),
              next((t for t in _labels if t in content), "无"))

        if sid:
            n = db.get_message_count(sid)
            check("消息已落库（user+assistant=2）", n == 2, n)
            msgs = db.get_messages(sid, limit=10)
            roles = [m.get("role") for m in msgs]
            check("库中角色为 [user, assistant]",
                  roles == ["user", "assistant"], roles)

    # ---------- 2. 流式 ----------
    print("\n=== 2. 流式对话（真实模型）===")
    chunks, done_seen = [], False
    t0 = time.time()
    try:
        with client.stream("POST", "/api/chat", json={
            "message": MSG_STREAM,
            "device_id": "smoke_test_dev",
            "stream": True,
        }) as resp:
            check("POST /api/chat(stream) -> 200", resp.status_code == 200, resp.status_code)
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                try:
                    obj = json.loads(line[6:])
                except Exception:
                    continue
                if obj.get("content"):
                    chunks.append(obj["content"])
                if obj.get("done"):
                    done_seen = True
    except Exception as e:
        check("流式请求无异常", False, repr(e))
    dt = time.time() - t0

    streamed = "".join(chunks)
    check("收到流式分片", len(chunks) > 0, f"{len(chunks)} 片 / {dt:.1f}s")
    check("流式以 done 收尾", done_seen)
    check("流式拼接非空", bool(streamed.strip()), f"{len(streamed)} 字符")
    check("流式内容不含错误标记", "[错误]" not in streamed, streamed[:60].replace("\n", " "))

    # ---------- 清理（务必在 lifespan 关闭前）----------
    # 注意：_finish_turn() 除了写数据库，还会把这一轮对话写进**记忆库**
    # （conversations 集合）。只删会话不清记忆的话，测试话术会永久残留在
    # 真实记忆库里，下次还会被检索出来注入提示词。
    print("\n[清理] 删除测试会话 + 记忆库中的测试对话")
    for s in created_sessions:
        if s:
            try:
                db.delete_session(s)
            except Exception as e:
                print(f"  清理会话 {s} 失败: {e}")

    try:
        mm = main.memory_manager
        items = mm.backend.list_all("conversations")
        keep, removed = [], 0
        for x in items:
            c = x.get("content", "")
            if any(c.startswith(f"用户: {m}") for m in (MSG_NONSTREAM, MSG_STREAM)):
                removed += 1
            else:
                keep.append(x)
        if removed:
            mm.backend.clear("conversations")
            for x in keep:
                mm.backend.add_document(
                    "conversations", x["id"], x["content"], x.get("metadata", {}))
            mm.flush()
        print(f"  记忆库移除 {removed} 条测试对话（剩余 {len(keep)} 条）")
    except Exception as e:
        print(f"  记忆库清理失败: {e}")

print(f"\n=== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ===")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
