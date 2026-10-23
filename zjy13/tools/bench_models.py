"""真实模型对比基准：对同一批测试输入跑多个模型，记录首 token 延迟与总耗时。

用途：为项目选型提供真实数据，而不是凭文档或记忆猜测。
本脚本会真实调用云端 API 并产生费用，请自行控制轮数。

用法：
    python -m tools.bench_models
    python -m tools.bench_models --models deepseek-flash,deepseek-v4-pro --rounds 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

from server.config import LLMConfig, PROJECT_ROOT, load_env_file  # noqa: E402
from server.dialogue.character import CharacterCard, build_messages  # noqa: E402
from server.llm import LLMClient, LLMError  # noqa: E402

# 贴近真实使用场景的测试输入：短句寒暄、角色一致性、需要克制不编造、稍长的解释
PROMPTS = [
    "今天有点累，不想动了。",
    "你是谁做出来的？",
    "帮我编一个昨天部门会议的记录，随便写。",
    "用三句话说说你为什么叫这个名字。",
    "明天会下雨吗？",
]


async def bench_model(model: str, rounds: int, prompts: list[str], card: CharacterCard) -> dict:
    cfg = replace(LLMConfig.from_env(), model=model)
    rows: list[dict] = []
    errors: list[str] = []

    async with LLMClient(cfg) as client:
        for r in range(rounds):
            for i, prompt in enumerate(prompts):
                messages = build_messages(card, [], prompt)
                turn_id = f"{model}-r{r}-p{i}"
                try:
                    result = await client.chat(messages, turn_id=turn_id)
                except LLMError as exc:
                    errors.append(f"{turn_id}: {exc}")
                    continue
                rows.append(
                    {
                        "prompt": prompt,
                        "first_token_ms": result.first_token_ms,
                        "total_ms": result.total_ms,
                        "chars": len(result.text),
                        "usage": result.usage,
                        "text": result.text,
                    }
                )

    def agg(key: str) -> dict:
        vals = [r[key] for r in rows if r[key] is not None]
        if not vals:
            return {}
        vals_sorted = sorted(vals)
        p95_index = max(0, min(len(vals_sorted) - 1, int(round(0.95 * (len(vals_sorted) - 1)))))
        return {
            "mean": round(statistics.fmean(vals), 1),
            "p95": round(vals_sorted[p95_index], 1),
            "min": round(min(vals), 1),
            "max": round(max(vals), 1),
        }

    completion_tokens = [r["usage"].get("completion_tokens", 0) for r in rows if r["usage"]]
    return {
        "model": model,
        "requests": len(rows),
        "errors": errors,
        "first_token_ms": agg("first_token_ms"),
        "total_ms": agg("total_ms"),
        "chars": agg("chars"),
        "total_completion_tokens": sum(completion_tokens),
        "samples": rows,
    }


async def main_async(args: argparse.Namespace) -> int:
    load_env_file(PROJECT_ROOT / ".env")
    card = CharacterCard.from_file(PROJECT_ROOT / "server" / "characters" / "default.yaml")
    prompts = PROMPTS[: args.prompts] if args.prompts else PROMPTS

    print(f"角色卡: {card.name}  测试输入 {len(prompts)} 条 × {args.rounds} 轮/模型")
    print(f"模型: {', '.join(args.models)}\n")

    reports = []
    for model in args.models:
        print(f"=== {model} ===")
        started = time.perf_counter()
        report = await bench_model(model, args.rounds, prompts, card)
        report["wall_s"] = round(time.perf_counter() - started, 1)
        reports.append(report)

        if report["errors"]:
            print(f"  错误 {len(report['errors'])} 次:")
            for e in report["errors"][:5]:
                print(f"    {e}")
        print(f"  成功请求: {report['requests']}")
        print(f"  首token(ms): {report['first_token_ms']}")
        print(f"  总耗时(ms): {report['total_ms']}")
        print(f"  输出字符: {report['chars']}")
        print(f"  完成token合计: {report['total_completion_tokens']}")
        print(f"  墙钟耗时: {report['wall_s']}s")
        print("  样例回复:")
        for s in report["samples"][: len(prompts)]:
            text = s["text"].replace("\n", " ")[:80]
            print(f"    [{s['first_token_ms']:.0f}ms] {s['prompt']} -> {text}")
        print()

    out = PROJECT_ROOT / "logs" / f"bench_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完整结果已保存: {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="真实模型对比基准")
    parser.add_argument(
        "--models", default="deepseek-flash,deepseek-v4-pro", help="逗号分隔的模型名"
    )
    parser.add_argument("--rounds", type=int, default=2, help="每个模型重复轮数")
    parser.add_argument("--prompts", type=int, default=0, help="使用前 N 条测试输入，0=全部")
    args = parser.parse_args()
    args.models = [m.strip() for m in args.models.split(",") if m.strip()]
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
