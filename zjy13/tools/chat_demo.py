"""终端交互式对话演示（真实调用云端 LLM）。

用法：
    $env:PYTHONUTF8="1"
    .\\.venv\\Scripts\\python.exe -m tools.chat_demo

内置命令：
    /reset    清空对话历史
    /stats    显示本轮会话统计
    /cancel   取消正在进行的回复
    /card     显示角色卡系统提示词
    /quit     退出

说明：这是**文字**演示，不含 ASR/TTS。语音链路属于后续阶段。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

from server.config import LLMConfig, PROJECT_ROOT, load_env_file  # noqa: E402
from server.dialogue.character import CharacterCard  # noqa: E402
from server.dialogue.session import ConversationSession  # noqa: E402
from server.llm import LLMClient, LLMConfigError, LLMError  # noqa: E402

BANNER = """
╭──────────────────────────────────────────────╮
│  云端 LLM 对话演示（文字版，不含 ASR/TTS）   │
│  /reset 清空历史   /stats 统计   /quit 退出  │
╰──────────────────────────────────────────────╯
"""


async def run(args: argparse.Namespace) -> int:
    try:
        load_env_file(PROJECT_ROOT / ".env")
    except LLMConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2

    try:
        cfg = LLMConfig.from_env()
        cfg.validate()
    except LLMConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2

    card_path = PROJECT_ROOT / "server" / "characters" / f"{args.character}.yaml"
    try:
        card = CharacterCard.from_file(card_path)
    except LLMConfigError as exc:
        print(f"[角色卡错误] {exc}", file=sys.stderr)
        return 2

    print(BANNER)
    print(f"模型: {cfg.model}   思考: {cfg.thinking}   流式: {cfg.stream}")
    print(f"角色: {card.name} ({card.character_id})   最大输出: {cfg.max_tokens} tokens\n")

    async with LLMClient(cfg) as client:
        session = ConversationSession(card, client, max_history_messages=args.history)

        while True:
            try:
                user_input = await asyncio.to_thread(input, "你 > ")
            except (EOFError, KeyboardInterrupt):
                print("\n再见。")
                break

            cmd = user_input.strip()
            if not cmd:
                continue
            if cmd in ("/quit", "/exit", "/q"):
                print("\n--- 会话统计 ---")
                for k, v in session.stats().items():
                    print(f"  {k}: {v}")
                break
            if cmd == "/reset":
                session.reset()
                print("[已清空对话历史]\n")
                continue
            if cmd == "/stats":
                for k, v in session.stats().items():
                    print(f"  {k}: {v}")
                print()
                continue
            if cmd == "/card":
                print("\n" + card.system_prompt() + "\n")
                continue
            if cmd == "/cancel":
                cancelled = await session.cancel()
                print(f"[{'已取消' if cancelled else '当前没有进行中的请求'}]\n")
                continue

            print(f"{card.name} > ", end="", flush=True)
            try:
                record = await session.send(cmd, on_delta=lambda t: print(t, end="", flush=True))
            except LLMError as exc:
                print(f"\n[调用失败] {exc.code}: {exc.message}")
                print("[本轮未计入历史，可重新输入]\n")
                continue
            except asyncio.CancelledError:
                print("\n[已取消]\n")
                continue

            print()
            first = f"{record.first_token_ms:.0f}ms" if record.first_token_ms else "n/a"
            print(f"  · 首字 {first} / 总 {record.total_ms:.0f}ms\n")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="云端 LLM 终端对话演示")
    parser.add_argument("--character", default="default", help="角色卡 ID（不含 .yaml）")
    parser.add_argument("--history", type=int, default=20, help="携带的历史消息条数上限")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
