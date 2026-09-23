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
        "message": "你好，请用一句话自我介绍",
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
            "message": "再说一句很短的话",
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
    print("\n[清理] 删除测试会话")
    for s in created_sessions:
        if s:
            try:
                db.delete_session(s)
            except Exception as e:
                print(f"  清理 {s} 失败: {e}")

print(f"\n=== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ===")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
