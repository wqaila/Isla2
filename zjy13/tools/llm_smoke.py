"""LLM 冒烟脚本：命令行验证一轮对话，打印计时与回复。

用法（真实服务商）：
    $env:LLM_BASE_URL="https://api.example.com/v1"
    $env:LLM_API_KEY="sk-..."
    $env:LLM_MODEL="your-model"
    python -m tools.llm_smoke "你好，介绍一下你自己"

用法（本地假服务端，先启动 tools.fake_llm_server）：
    $env:LLM_BASE_URL="http://127.0.0.1:8765/v1"
    $env:LLM_API_KEY="local-test-key"
    $env:LLM_MODEL="fake-model"
    python -m tools.llm_smoke "你好" --no-stream

退出码：0 成功；2 配置缺失；3 调用失败。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

# 允许直接以脚本方式运行（python tools/llm_smoke.py）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 控制台默认 GBK，中文会乱码；统一改为 UTF-8 输出
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

from dotenv import load_dotenv  # noqa: E402

from server.config import LLMConfig, PROJECT_ROOT, load_env_file  # noqa: E402
from server.dialogue.character import CharacterCard, build_messages  # noqa: E402
from server.llm import LLMClient, LLMConfigError, LLMError  # noqa: E402


def _load_env() -> None:
    load_env_file(PROJECT_ROOT / ".env")


async def run(args: argparse.Namespace) -> int:
    try:
        _load_env()
    except LLMConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = LLMConfig.from_env()
    except LLMConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2

    missing = cfg.missing_fields()
    if missing:
        print("[配置缺失] 未设置: " + ", ".join(missing), file=sys.stderr)
        print("请复制 .env.example 为 .env 并填写实际服务商信息。", file=sys.stderr)
        return 2

    if args.no_stream:
        cfg = replace(cfg, stream=False)

    print("配置快照（已脱敏）:")
    for key, value in cfg.redacted().items():
        print(f"  {key}: {value}")

    char_dir = Path(os.environ.get("CHARACTERS_DIR", "server/characters"))
    if not char_dir.is_absolute():
        char_dir = PROJECT_ROOT / char_dir
    char_id = os.environ.get("DEFAULT_CHARACTER_ID", "default")
    card_path = char_dir / f"{char_id}.yaml"
    try:
        card = CharacterCard.from_file(card_path)
    except LLMConfigError as exc:
        print(f"[角色卡错误] {exc}", file=sys.stderr)
        return 2

    print(f"\n角色卡: {card.name} ({card.character_id}) prompt_version={card.prompt_version()}")
    messages = build_messages(card, [], args.text)

    streamed: list[str] = []

    def on_delta(text: str) -> None:
        streamed.append(text)
        if not args.no_stream:
            print(text, end="", flush=True)

    print("\n--- 回复 ---")
    async with LLMClient(cfg) as client:
        try:
            result = await client.chat(
                messages,
                turn_id=f"smoke-{os.getpid()}",
                on_delta=None if args.no_stream else on_delta,
            )
        except LLMError as exc:
            print(f"\n[调用失败] {exc}", file=sys.stderr)
            print(f"错误详情: {exc.to_dict()}", file=sys.stderr)
            return 3

    if args.no_stream:
        print(result.text)
    print("\n--- 计时 ---")
    for key, value in result.summary().items():
        print(f"  {key}: {value}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="云端 LLM 冒烟测试")
    parser.add_argument("text", nargs="?", default="你好，简单介绍一下你自己。", help="用户输入")
    parser.add_argument("--no-stream", action="store_true", help="使用非流式请求")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
