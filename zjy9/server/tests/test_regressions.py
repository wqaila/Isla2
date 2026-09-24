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

# 测试创建过的会话 id：外层 finally 用它**精确**清理记忆库条目。
# ⚠️ 不要按内容关键词清理 —— 原过滤器里带 "咖啡" 条件，
# 用户只要记过跟咖啡有关的真实记忆，跑一次测试就会被删掉。
_TEST_SESSION_IDS = set()

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
_TEST_SESSION_IDS.add(sid)
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

    # 回归 3：注入提示词的记忆/画像上下文不得带 [标签]。
    # 模型会把方括号标签当成自己的输出格式照抄（实测回复里冒出过 "[历史对话]"）。
    from memory import strip_leading_tag
    check("strip_leading_tag 剥掉遗留标签",
          strip_leading_tag("[姓名] 舰长叫小明") == "舰长叫小明")
    check("strip_leading_tag 支持多重标签",
          strip_leading_tag("[AI推断-偏好] [兴趣] 喜欢咖啡") == "喜欢咖啡")
    check("strip_leading_tag 不改正常文本",
          strip_leading_tag("舰长喜欢咖啡") == "舰长喜欢咖啡")

    main.memory_manager.add_fact("[测试标签] __测试注入标签__", source="test")
    mem_ctx = main.memory_manager.get_memory_context("__测试注入标签__", max_chars=800)
    check("记忆上下文不含 [标签]", "[" not in mem_ctx, mem_ctx[:60])

    learn_ctx = main.learning_engine.get_personalized_context(
        current_message="你好", max_chars=600)
    check("学习画像上下文不含 [标签]", "[" not in learn_ctx, learn_ctx[:60])

    from elysia_prompt import build_messages
    sys_prompt = build_messages(
        "你好", history=[], few_shot=False,
        memory_context=mem_ctx, learning_context=learn_ctx,
    )[0]["content"]
    check("最终 system 提示词不含 [标签]", "[" not in sys_prompt)

    print("\n=== 6. 模型调用重试（无需网络）===")
    import asyncio as _asyncio
    import httpx as _httpx
    from retry import retry_async, is_retryable

    class _Flaky:
        """前 fail_times 次抛指定异常，之后成功"""
        def __init__(self, exc, fail_times):
            self.exc, self.fail_times, self.calls = exc, fail_times, 0

        async def __call__(self):
            self.calls += 1
            if self.calls <= self.fail_times:
                raise self.exc
            return "ok"

    f = _Flaky(_httpx.ConnectError("boom"), 2)
    r = _asyncio.run(retry_async(f, attempts=3, base_delay=0.01))
    check("连接错误会重试并最终成功", r == "ok" and f.calls == 3, f"calls={f.calls}")

    _req = _httpx.Request("POST", "http://x")
    f = _Flaky(_httpx.HTTPStatusError("401", request=_req,
                                      response=_httpx.Response(401, request=_req)), 5)
    try:
        _asyncio.run(retry_async(f, attempts=3, base_delay=0.01))
        raised = False
    except _httpx.HTTPStatusError:
        raised = True
    check("4xx 不重试（立即失败，不浪费时间）", raised and f.calls == 1, f"calls={f.calls}")

    f = _Flaky(_httpx.HTTPStatusError("503", request=_req,
                                      response=_httpx.Response(503, request=_req)), 1)
    r = _asyncio.run(retry_async(f, attempts=3, base_delay=0.01))
    check("5xx 会重试", r == "ok" and f.calls == 2, f"calls={f.calls}")

    f = _Flaky(_httpx.ReadTimeout("t"), 9)
    try:
        _asyncio.run(retry_async(f, attempts=2, base_delay=0.01))
        raised = False
    except _httpx.ReadTimeout:
        raised = True
    check("重试耗尽后抛出最后一次异常", raised and f.calls == 2, f"calls={f.calls}")

    check("is_retryable 判定正确",
          is_retryable(_httpx.ConnectError("x")) and not is_retryable(ValueError("x")))

    print("\n=== 7. 记忆单条删除 ===")
    # 注意：第 4 节把 api_token 设成了非空，所以这里的 HTTP 请求必须带认证头。
    _auth = {"Authorization": f"Bearer {main._current_api_token()}"}

    main.memory_manager.add_fact("__测试删除目标__", source="test")
    main.memory_manager.flush()

    r = client.get("/api/memory/entries", params={"collection": "facts", "limit": 500},
                   headers=_auth)
    check("列出记忆条目 -> 200", r.status_code == 200, r.status_code)
    items = r.json().get("items", []) if r.status_code == 200 else []
    target = next((x for x in items if "__测试删除目标__" in x.get("content", "")), None)
    check("能列出刚写入的条目", target is not None)

    if target:
        r = client.delete(f"/api/memory/entries/facts/{target['id']}", headers=_auth)
        check("删除单条 -> 200", r.status_code == 200, r.status_code)

        r2 = client.get("/api/memory/entries", params={"collection": "facts", "limit": 500},
                        headers=_auth)
        left = [x for x in r2.json().get("items", [])
                if "__测试删除目标__" in x.get("content", "")]
        check("删除后不再出现在列表里", len(left) == 0, len(left))

        r3 = client.delete(f"/api/memory/entries/facts/{target['id']}", headers=_auth)
        check("重复删除 -> 404", r3.status_code == 404, r3.status_code)

    r = client.delete("/api/memory/entries/evil/abc", headers=_auth)
    check("非法集合删除 -> 400", r.status_code == 400, r.status_code)
    r = client.get("/api/memory/entries", params={"collection": "evil"}, headers=_auth)
    check("非法集合列表 -> 400", r.status_code == 400, r.status_code)

    print("\n=== 8. 对话导出与数据库备份 ===")
    _sid = db.create_session("export_test", "导出测试")
    _TEST_SESSION_IDS.add(_sid)
    db.auto_title_session(_sid, "导出功能测试")
    db.save_message(_sid, "user", "导出测试用户消息")
    db.save_message(_sid, "assistant", "导出测试助手回复", 42, 800)

    r = client.get(f"/api/sessions/{_sid}/export", headers=_auth)
    check("导出单会话 JSON -> 200", r.status_code == 200, r.status_code)
    if r.status_code == 200:
        d = r.json()
        check("导出包含 2 条消息", d.get("message_count") == 2, d.get("message_count"))
        check("响应带附件下载头",
              "attachment" in (r.headers.get("content-disposition") or ""),
              r.headers.get("content-disposition"))

    r = client.get(f"/api/sessions/{_sid}/export", params={"format": "md"},
                   headers=_auth)
    check("导出 Markdown -> 200", r.status_code == 200, r.status_code)
    if r.status_code == 200:
        check("Markdown 含对话正文", "导出测试用户消息" in r.text)

    r = client.get("/api/sessions/__no_such_session__/export", headers=_auth)
    check("导出不存在的会话 -> 404", r.status_code == 404, r.status_code)

    r = client.get("/api/export/all", headers=_auth)
    check("导出全部对话 -> 200", r.status_code == 200, r.status_code)
    if r.status_code == 200:
        check("导出全部含刚建的会话",
              any(x["session"]["id"] == _sid for x in r.json().get("sessions", [])))

    r = client.post("/api/db/backup", params={"keep": 7}, headers=_auth)
    check("数据库备份 -> 200", r.status_code == 200, r.status_code)
    created_backup = r.json().get("file") if r.status_code == 200 else None
    check("备份文件非空",
          bool(created_backup) and r.json().get("size_bytes", 0) > 0,
          r.json().get("size_bytes") if r.status_code == 200 else None)

    r = client.get("/api/db/backups", headers=_auth)
    check("备份列表 -> 200", r.status_code == 200, r.status_code)
    if r.status_code == 200:
        check("列表含刚生成的备份",
              any(b["file"] == created_backup for b in r.json().get("backups", [])))

    db.delete_session(_sid)
    if created_backup:      # 清掉本次测试产生的备份，避免每跑一次就多一份
        try:
            (db.BACKUP_DIR / created_backup).unlink()
        except Exception:
            pass

    print("\n=== 9. 混合检索（BM25 + 向量）===")
    import tempfile as _tf
    from pathlib import Path as _P
    from memory import TfidfBackend, _tokenize, _BM25

    check("中文分词含单字与相邻双字", _tokenize("咖啡") == ["咖", "啡", "咖啡"],
          _tokenize("咖啡"))
    check("英文按整词切并转小写", "coffee" in _tokenize("Coffee 2024"))

    _bm = _BM25([_tokenize(d) for d in
                 ["咖啡", "咖啡今天天气不错适合出门散步走一走", "无关内容"]])
    _sc = _bm.scores(_tokenize("咖啡"))
    check("BM25 文档长度归一化生效", _sc[0] > _sc[1], f"{_sc[0]:.3f} vs {_sc[1]:.3f}")
    check("BM25 无关文档得 0 分", _sc[2] == 0.0, _sc[2])

    _TOPICS = [
        ("咖啡", "喜欢喝咖啡，每天早上都要来一杯"),
        ("猫咪", "养了一只叫豆豆的橘猫"),
        ("钢琴", "小时候学过钢琴，考过八级"),
        ("火锅", "最爱吃重庆火锅，越辣越好"),
        ("日语", "在自学日语，准备考N2"),
        ("象棋", "爷爷教我下中国象棋"),
        ("天文", "对天文很感兴趣，有台望远镜"),
        ("登山", "去年登过泰山看日出"),
        ("钓鱼", "爸爸喜欢钓鱼，我偶尔陪他去"),
        ("滑雪", "冬天想去滑雪"),
        ("电影", "喜欢看悬疑电影"),
        ("旅行", "想去冰岛看极光"),
    ]
    _tmp = _tf.TemporaryDirectory()
    _b = TfidfBackend()
    _b.db_path = _P(_tmp.name)
    _b._data = {c: {} for c in ("conversations", "facts", "summaries")}
    _b._version = {c: 0 for c in _b._data}
    _b._dirty, _b._last_flush, _b._tfidf_cache = set(), {}, {}
    for _i, (_kw, _txt) in enumerate(_TOPICS):
        _b.add_document("facts", f"t{_i:02d}", _txt, {})

    # 回归守卫：查询「猫咪」的二元组与文档里的「橘猫」不重叠，
    # 纯向量会算成相似度 0 并被旧阈值过滤掉 —— 一条都召回不了。
    # BM25 的单字词元补上了这个洞。
    cp.save_config({"memory_hybrid_search": True})
    _r = _b.search("facts", "猫咪", n_results=3)
    check("混合检索能召回『猫咪』（纯向量会漏）",
          bool(_r) and _r[0]["id"] == "t01", [x["id"] for x in _r])

    def _top1(hybrid):
        cp.save_config({"memory_hybrid_search": hybrid})
        hits = 0
        for _i, (_kw, _) in enumerate(_TOPICS):
            _res = _b.search("facts", _kw, n_results=3)
            if _res and _res[0]["id"] == f"t{_i:02d}":
                hits += 1
        return hits

    _h, _v = _top1(True), _top1(False)
    cp.save_config({"memory_hybrid_search": True})
    _tmp.cleanup()

    check(f"混合检索 Top1 不低于纯向量（{_h} vs {_v}）", _h >= _v, f"{_h} vs {_v}")
    check("混合检索 Top1 命中率 >= 70%",
          _h >= int(len(_TOPICS) * 0.7), f"{_h}/{len(_TOPICS)}")

    print("\n=== 10. 本地 Ollama 不受系统代理影响 ===")
    # httpx 默认 trust_env=True，会读取 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY，
    # 把**本机**的 Ollama 也塞进代理 —— 结果整个本地推理返回 502，
    # 而报错信息是 "Ollama API 返回 502"，完全看不出是代理导致的。
    # 用户只要开了全局代理/VPN，本地模型就整个不可用。
    from ollama_client import ollama_client as _oc
    check("ollama 客户端已关闭 trust_env（不读系统代理）",
          getattr(_oc._client, "trust_env", None) is False,
          getattr(_oc._client, "trust_env", "无此属性"))

    print("\n=== 11. 会话中期摘要（分层记忆）===")
    # 说明：这里**不调用模型** —— 只验证存取、切片、注入与开关这些确定性逻辑。
    # 真实的摘要生成质量已单独实测过（基座模型准确；人设 LoRA 会编造日期，
    # 所以摘要刻意不走人设模型）。
    from memory import memory_manager as _mm
    import asyncio as _aio

    _sid2 = db.create_session("summary_reg", "摘要回归")
    _TEST_SESSION_IDS.add(_sid2)
    for _i in range(6):
        db.save_message(_sid2, "user", f"第{_i}条用户消息")
        db.save_message(_sid2, "assistant", f"第{_i}条回复")

    _part = db.get_messages_range(_sid2, 2, 3)
    check("get_messages_range 切片正确",
          [m["content"] for m in _part]
          == ["第1条用户消息", "第1条回复", "第2条用户消息"],
          [m["content"] for m in _part])
    check("get_messages_range limit=0 返回空",
          db.get_messages_range(_sid2, 0, 0) == [])

    check("初始没有会话摘要", _mm.get_session_summary(_sid2) is None)

    def _count_summaries():
        return sum(1 for x in _mm.backend.list_all("summaries")
                   if (x.get("metadata") or {}).get("session_id") == _sid2)

    _mm.set_session_summary(_sid2, "用户面试了杭州的机器人公司，下周三去上海出差。",
                            covered_until=7)
    _got = _mm.get_session_summary(_sid2)
    check("写入后能读回摘要", bool(_got) and "上海出差" in _got["content"],
          (_got or {}).get("content", "")[:40])
    check("覆盖范围被正确记录", bool(_got) and _got["covered_until"] == 7, _got)
    check("每个会话只保留一份摘要", _count_summaries() == 1, _count_summaries())

    _mm.set_session_summary(_sid2, "更新后的摘要内容", covered_until=9)
    check("重复写入是覆盖而非新增", _count_summaries() == 1, _count_summaries())

    _ctx = _aio.run(main._build_chat_context(_sid2, "summary_reg", "下周三我要干嘛"))
    _mc = _ctx["memory_context"]
    check("摘要被注入到 memory_context",
          "更早之前聊过的" in _mc and "更新后的摘要内容" in _mc, _mc[:60])
    check("注入内容不含方括号标签（模型会照抄）", "[" not in _mc)

    # 开关关闭时不应该更新摘要（也顺带证明不会去调模型）
    _mm.set_session_summary(_sid2, "占位内容", covered_until=0)
    cp.save_config({"memory_session_summary": False})
    try:
        _aio.run(main._maybe_update_session_summary(_sid2))
        _after = _mm.get_session_summary(_sid2)
        check("开关关闭时不更新摘要",
              bool(_after) and _after["covered_until"] == 0, _after)
    finally:
        cp.save_config({"memory_session_summary": True})

    db.delete_session(_sid2)
    for _x in _mm.backend.list_all("summaries"):
        if (_x.get("metadata") or {}).get("session_id") == _sid2:
            _mm.delete_entry("summaries", _x["id"])
    _mm.flush()

finally:
    # 清理测试数据
    for _s in _TEST_SESSION_IDS:
        db.delete_session(_s)
    mm = main.memory_manager
    # 用单条删除接口 + 会话 id 精确匹配。
    # 之前是"按内容关键词过滤 + 清空集合再写回"：既会误删用户的真实记忆
    # （过滤器里有 "咖啡"），又会在中途出错时把整个记忆库搞坏。
    for coll in ["facts", "conversations", "summaries"]:
        for _x in mm.list_all(coll):
            _meta = _x.get("metadata") or {}
            if (_meta.get("session_id") in _TEST_SESSION_IDS
                    or "__测试" in (_x.get("content") or "")):
                mm.delete_entry(coll, _x["id"])
    mm.flush()
    cp.save_config({"rate_limit_per_minute": 120, "api_token": ""})
    print("\n[清理] 测试会话/记忆已删除，运行时配置已还原")

print(f"\n=== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ===")
if FAIL:
    print("失败项:")
    for f in FAIL:
        print("  -", f)
