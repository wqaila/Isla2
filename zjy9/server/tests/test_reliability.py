"""可靠性测试：就绪探针 /ready + 生成并发闸门。

闸门的纯逻辑部分在独立事件循环里测（确定性、快）；之后必须把
_generation_semaphore 复位为 None，否则会把上一个 loop 的 Semaphore
带进 TestClient 自己的 loop，导致行为异常。

运行：cd server && ./venv/Scripts/python.exe tests/test_reliability.py
"""
import asyncio
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER))

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
import config_persistence as cp  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  -> ' + str(extra)) if extra else ''}")


print("=== 1. 生成闸门（纯逻辑）===")


async def _gate():
    main._generation_semaphore = asyncio.Semaphore(1)
    main._generation_inflight = 0
    a = await main._acquire_generation_slot()
    b = await main._acquire_generation_slot()          # 满了 → 应立即失败
    stats_full = main.generation_stats()
    main._release_generation_slot()
    c = await main._acquire_generation_slot()          # 释放后可再占
    main._release_generation_slot()
    return a, b, c, stats_full


a, b, c, stats_full = asyncio.run(_gate())
check("空位可占用", a is True)
check("满位快速失败（不排队）", b is False)
check("占用情况可见", stats_full["limit"] == 1 and stats_full["inflight"] == 1
      and stats_full["available"] == 0, stats_full)
check("释放后可再次占用", c is True)
check("全部释放后归零", main.generation_stats()["inflight"] == 0,
      main.generation_stats()["inflight"])

# 复位：让应用在自己的事件循环里重新创建 Semaphore
main._generation_semaphore = None
main._generation_inflight = 0

print("\n=== 2. 就绪探针 /ready ===")
with TestClient(main.app) as client:
    r = client.get("/ready")
    check("/ready 返回 200 或 503", r.status_code in (200, 503), r.status_code)
    body = r.json()
    checks = body.get("checks", {})
    check("/ready 含 database / ollama / cloud / model_ready",
          all(k in checks for k in ("database", "ollama", "cloud", "model_ready")),
          list(checks))
    check("/ready 含生成闸门状态", "generation" in checks)
    check("/ready 的 status 与状态码一致",
          (body.get("status") == "ready") == (r.status_code == 200), body.get("status"))
    check("数据库检查通过", checks.get("database") is True, checks.get("database"))

    print("\n=== 3. 探针免认证（但要认证的接口仍然要认证）===")
    cp.save_config({"api_token": "__probe_token__"})
    try:
        r2 = client.get("/ready")
        check("/ready 免认证", r2.status_code in (200, 503), r2.status_code)
        r3 = client.get("/health")
        check("/health 免认证", r3.status_code == 200, r3.status_code)
        r4 = client.get("/api/status")
        check("/api/status 仍要求认证（401）", r4.status_code == 401, r4.status_code)
        r5 = client.get("/ready", headers={"Authorization": "Bearer __probe_token__"})
        check("带令牌访问 /ready 也正常", r5.status_code in (200, 503), r5.status_code)
    finally:
        cp.save_config({"api_token": ""})

print("\n=== 4. 闸门不会误伤正常请求（真实模型）===")


def _ollama_alive() -> bool:
    """不依赖事件循环地探测 Ollama（避免和 TestClient 的 loop 打架）"""
    import json as _json
    import urllib.request as _ur
    try:
        with _ur.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            _json.loads(r.read())
        return True
    except Exception:
        return False


WS_MSG = "只回两个字"

if not _ollama_alive():
    print("  SKIP: 本机 Ollama 未运行，跳过真实模型相关用例（不算失败）")
else:
    import time as _time
    import database as _db

    with TestClient(main.app) as client:
        r = client.post("/api/chat", json={
            "message": "只回一个字",
            "device_id": "gate_test",
            "stream": False,
        })
        check("正常单发请求不被闸门挡住", r.status_code == 200, r.status_code)
        if r.status_code == 200:
            sid = r.json().get("session_id")
            if sid:
                _db.delete_session(sid)
        check("请求结束后槽位已释放", main.generation_stats()["inflight"] == 0,
              main.generation_stats())

    print("\n=== 5. WebSocket 路径：闸门不泄漏 + 落库正确 ===")
    with TestClient(main.app) as client:
        with client.websocket_connect(
                "/ws/chat?device_id=ws_gate_test&device_name=闸门测试") as ws:
            hello = ws.receive_json()
            ws_sid = hello.get("session_id")
            check("WS 欢迎消息 type=connected", hello.get("type") == "connected",
                  hello.get("type"))
            ws.send_json({"type": "chat", "message": WS_MSG})
            got, done = [], False
            for _ in range(600):
                m = ws.receive_json()
                if m.get("content"):
                    got.append(m["content"])
                if m.get("done"):
                    done = True
                    break
            check("WS 收到流式分片", len(got) > 0, len(got))
            check("WS 以 done 收尾", done)

        # 关键：_finish_turn 在 done 之后才跑，必须等服务端收尾再断言，
        # 否则会误判成"槽位泄漏"（这是个容易踩的测试竞态）。
        for _ in range(50):
            if main.generation_stats()["inflight"] == 0:
                break
            _time.sleep(0.1)
        check("WS 结束后闸门槽位已释放",
              main.generation_stats()["inflight"] == 0, main.generation_stats())

        msgs = _db.get_messages(ws_sid, limit=10)
        check("WS 消息已落库（user + assistant）",
              [m["role"] for m in msgs] == ["user", "assistant"],
              [m["role"] for m in msgs])

        # 清理：会话 + 记忆库（_finish_turn 也会写记忆）
        _db.delete_session(ws_sid)
        mm = main.memory_manager
        items = mm.backend.list_all("conversations")
        keep = [x for x in items
                if not x.get("content", "").startswith(f"用户: {WS_MSG}")]
        if len(keep) != len(items):
            mm.backend.clear("conversations")
            for x in keep:
                mm.backend.add_document(
                    "conversations", x["id"], x["content"], x.get("metadata", {}))
            mm.flush()

print("\n=== 6. WebSocket 鉴权（不需要模型）===")
# 背景：HTTP 中间件管不到 WebSocket，两个 WS 端点此前完全不校验令牌，
# 而 /ws/logs 会把聊天正文广播给订阅者 —— 开了公网隧道就等于把对话内容
# 公开出去。这里逐条验证修复后的行为。
_WS_TOK = "__ws_auth_test_token__"


def _ws_probe(path: str, read_msg: bool = False):
    """返回 True 表示连接建立成功（未被拒绝）"""
    from starlette.websockets import WebSocketDisconnect as _WSD
    try:
        with TestClient(main.app) as c:
            with c.websocket_connect(path) as ws:
                if read_msg:
                    ws.receive_json()
        return True
    except _WSD:
        return False
    except Exception:
        return False


_orig_tok = cp.load_config().get("api_token", "")
try:
    cp.save_config({"api_token": ""})
    check("未设令牌时 /ws/chat 放行（向后兼容）",
          _ws_probe("/ws/chat?device_id=t", read_msg=True) is True)
    check("未设令牌时 /ws/logs 放行（向后兼容）",
          _ws_probe("/ws/logs") is True)

    cp.save_config({"api_token": _WS_TOK})
    check("设令牌后 /ws/chat 无令牌被拒",
          _ws_probe("/ws/chat?device_id=t") is False)
    check("设令牌后 /ws/chat 错误令牌被拒",
          _ws_probe("/ws/chat?device_id=t&token=wrong") is False)
    check("设令牌后 /ws/chat 正确令牌放行",
          _ws_probe(f"/ws/chat?device_id=t&token={_WS_TOK}", read_msg=True) is True)
    check("设令牌后 /ws/logs 无令牌被拒（聊天正文不再泄露）",
          _ws_probe("/ws/logs") is False)
    check("设令牌后 /ws/logs 正确令牌放行",
          _ws_probe(f"/ws/logs?token={_WS_TOK}") is True)
finally:
    cp.save_config({"api_token": _orig_tok})

print(f"\n=== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ===")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
