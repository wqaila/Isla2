"""验证 ollama_client 的流式截断逻辑：不再重复输出、切片索引正确

运行：cd server && ./venv/Scripts/python.exe tests/test_stream_truncation.py
"""
import sys
import asyncio
import json
from pathlib import Path

# 测试脚本位于 server/tests/，需要把 server/ 加进 import 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ollama_client as oc

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  -> ' + str(extra)) if extra else ''}")


class FakeResponse:
    def __init__(self, lines, status=200):
        self._lines = lines
        self.status_code = status

    async def aclose(self):
        pass

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aiter_text(self):
        for ln in self._lines:
            yield ln


class FakeStreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *a):
        return False


class FakeClient:
    """httpx.AsyncClient 的替身。

    注意：ollama_client 的流式路径现在走 `build_request` + `send(stream=True)`，
    而不是 `client.stream()` 上下文管理器 —— 目的是让「建连阶段」可以被重试
    （上下文管理器进不去就没法重试）。所以替身必须实现这两个方法。
    """

    def __init__(self, lines, status=200):
        self._lines = lines
        self._status = status
        self.sent_requests = 0

    def build_request(self, *a, **kw):
        return {"method": a[0] if a else "POST", "kwargs": kw}

    async def send(self, request, stream=False, **kw):
        self.sent_requests += 1
        return FakeResponse(self._lines, self._status)

    async def stream(self, *a, **kw):
        return FakeStreamCtx(FakeResponse(self._lines, self._status))

    async def aclose(self):
        pass


def make_lines(chunks, eval_count=100):
    lines = []
    for c in chunks:
        lines.append(json.dumps({"message": {"content": c}, "done": False}))
    lines.append(json.dumps({"message": {"content": ""}, "done": True, "eval_count": eval_count}))
    return lines


async def collect(client, messages):
    c = oc.OllamaClient()
    c._client = client
    out = []
    async for chunk in c.chat(messages, stream=True):
        out.append(chunk)
    return out


print("\n=== 1. 动态长度映射与人设约束一致 ===")
mapping = [(3, 33), (10, 56), (30, 90), (100, 110), (300, 143)]
for n, expect in mapping:
    got = oc._calc_dynamic_num_predict(n)
    check(f"输入{n}字符 -> {got} token（期望{expect}）", got == expect, got)
check("MAX_OUTPUT_CHARS 与 200 字上限量级匹配", oc.MAX_OUTPUT_CHARS == 400, oc.MAX_OUTPUT_CHARS)
check("num_ctx 已提升到 4096", oc.GENERATION_OPTIONS["num_ctx"] == 4096,
      oc.GENERATION_OPTIONS["num_ctx"])

print("\n=== 2. 正常流式：内容不重复、不丢字 ===")
chunks = ["你好", "呀，", "舰长", "！"]
res = asyncio.run(collect(FakeClient(make_lines(chunks)), [{"role": "user", "content": "你好"}]))
streamed = "".join(c["content"] for c in res if c.get("content"))
check("流式拼接结果与原文一致", streamed == "".join(chunks), repr(streamed))
check("末尾有 done 标记", res[-1].get("done") is True)

print("\n=== 3. 超长输出：截断后不重复整段（核心 bug）===")
# 构造一段超长且带重复的内容，触发 MAX_OUTPUT_CHARS 硬截断
long_chunks = ["测试内容" * 40] * 20   # 160*20 = 3200 字符
res = asyncio.run(collect(FakeClient(make_lines(long_chunks)), [{"role": "user", "content": "长"}]))
streamed = "".join(c["content"] for c in res if c.get("content"))
check(f"输出被硬限制在 {oc.MAX_OUTPUT_CHARS} 附近", len(streamed) <= oc.MAX_OUTPUT_CHARS,
      f"实际 {len(streamed)}")
# 关键：不能出现"同一段文本被发两次"
first_part = streamed[:200]
occurrences = streamed.count(first_part)
check("开头 200 字符在输出中只出现一次（没有重复整段）", occurrences == 1, f"出现 {occurrences} 次")

print("\n=== 4. 重复内容检测：末尾不再重发整段 ===")
# 让生成内容触发重复检测，然后正常 done
rep_chunks = ["爱莉希雅"] * 30
res = asyncio.run(collect(FakeClient(make_lines(rep_chunks)), [{"role": "user", "content": "重复"}]))
streamed = "".join(c["content"] for c in res if c.get("content"))
check("重复场景输出长度不超过硬上限", len(streamed) <= oc.MAX_OUTPUT_CHARS, len(streamed))
check("没有把整段已输出内容再发一遍（长度未翻倍）",
      len(streamed) <= len("".join(rep_chunks)), f"{len(streamed)} vs {len(''.join(rep_chunks))}")
check("重复场景仍以 done 收尾", res[-1].get("done") is True)

print(f"\n=== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ===")
for f in FAIL:
    print("  - 失败:", f)
