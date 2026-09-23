"""测试汇总入口：依次执行 tests/ 下的全部脚本并汇总结果。

为什么不是 pytest：
    这些脚本是"独立脚本"风格（模块级执行 + check() 累加结果），要变成 pytest
    用例得把每个脚本整体重构成函数，改动大、风险高、收益低。汇总入口已经
    满足 CI 的核心需求：**一条命令 + 可靠的退出码**。

用法：
    cd server && ./venv/Scripts/python.exe run_tests.py
退出码：
    0 = 全部通过（含自动跳过）；1 = 有脚本失败
"""
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent
TESTS = SERVER / "tests"

# (脚本名, 说明, 是否需要 Ollama)
SUITE = [
    ("test_regressions.py", "接口 / 数据层 / 提示词 / 重试回归", False),
    ("test_stream_truncation.py", "流式截断逻辑", False),
    ("test_lifespan_smoke.py", "启动与优雅关闭", False),
    ("test_data_retention.py", "数据保留（临时库）", False),
    ("test_reliability.py", "就绪探针 / 并发闸门 / WS 鉴权", True),
    ("test_chat_e2e.py", "端到端对话（真实模型）", True),
]


def _summary_lines(text: str) -> list:
    """挑出每个脚本的结果行（结果/失败/SKIP），避免刷屏"""
    keys = ("=== 结果", "失败项", "SKIP")
    return [ln for ln in text.splitlines() if any(k in ln for k in keys)]


def main() -> int:
    results = []
    for name, desc, needs_model in SUITE:
        path = TESTS / name
        if not path.exists():
            print(f"\n⚠️  跳过 {name}：文件不存在")
            results.append((name, "缺失", 1))
            continue

        print(f"\n{'=' * 66}")
        print(f"▶ {name} —— {desc}" + ("（需要 Ollama）" if needs_model else ""))
        print("=" * 66)

        proc = subprocess.run(
            [sys.executable, str(path)], cwd=str(SERVER),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        lines = _summary_lines(out)

        if proc.returncode == 0:
            status = "通过"
            for ln in lines:
                print(f"  {ln.strip()}")
        else:
            status = "失败"
            # 失败时把完整输出打出来，方便定位
            print(out)

        results.append((name, status, proc.returncode))

    print(f"\n{'=' * 66}")
    print("汇总")
    print("=" * 66)
    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, status, code in results:
        mark = "✅" if code == 0 else "❌"
        print(f"  {mark} {name:<{width}}  {status}")
        if code != 0:
            failed += 1

    total = len(results)
    print(f"\n{total - failed}/{total} 个脚本通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
