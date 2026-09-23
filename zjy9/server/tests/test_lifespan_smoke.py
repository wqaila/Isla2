"""lifespan 启动/关闭冒烟测试（此前完全没覆盖的路径）。

背景：其余测试用 `TestClient(main.app)` 而没有用 `with` 上下文管理器，
而 FastAPI 只在上下文管理器进入/退出时才执行 lifespan。因此启动逻辑
（建目录 / 建库 / 日志循环绑定 / Ollama 探测 / 清理任务 / 优雅关闭）
从未被真正执行过——这里补上。

运行：cd server && ./venv/Scripts/python.exe tests/test_lifespan_smoke.py
"""
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER))

from fastapi.testclient import TestClient
import main

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  -> ' + str(extra)) if extra else ''}")


print("=== lifespan 启动 -> 请求 -> 关闭 ===")
try:
    with TestClient(main.app) as client:
        check("lifespan 启动阶段无异常", True)

        r = client.get("/health")
        check("GET /health -> 200", r.status_code == 200, r.status_code)

        r = client.get("/api/prompt/current")
        check("GET /api/prompt/current -> 200", r.status_code == 200, r.status_code)
        if r.status_code == 200:
            check("提示词含真实称呼'舰长'", "舰长" in r.text)

        r = client.get("/static/index.html")
        check("GET /static/index.html -> 200", r.status_code == 200, r.status_code)

        r = client.get("/dashboard")
        check("GET /dashboard -> 200", r.status_code == 200, r.status_code)
    check("lifespan 关闭阶段无异常（优雅关闭跑通）", True)
except Exception as e:
    check("lifespan 全程无异常", False, repr(e))

print(f"\n=== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ===")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
