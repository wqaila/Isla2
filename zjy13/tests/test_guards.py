"""对话前置检查测试：空历史 + 记忆类问题的拦截。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from server.dialogue.character import CharacterCard
from server.dialogue.guards import empty_history_reply, is_memory_question
from server.dialogue.session import ConversationSession
from server.llm.base import LLMResult

CARD_PATH = Path(__file__).resolve().parent.parent / "server" / "characters" / "default.yaml"


class FakeClient:
    def __init__(self, reply: str = "模型回复") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    async def chat(self, messages, *, turn_id=None, on_delta=None, **kw):
        self.calls.append({"messages": list(messages), "turn_id": turn_id})
        if on_delta:
            on_delta(self.reply)
        return LLMResult(
            text=self.reply, model="fake", finish_reason="stop",
            first_token_ms=50.0, total_ms=100.0,
        )

    async def cancel(self) -> bool:
        return True


@pytest.mark.parametrize(
    "text",
    [
        "你还记得我刚才在做什么吗？",
        "我刚才说了什么",
        "我们之前聊过什么？",
        "记不记得我说过的事",
        "我叫什么名字？",
        "我名字是啥",
        "我刚刚说了啥",
        "之前提到的那件事",
    ],
)
def test_detects_memory_questions(text: str) -> None:
    assert is_memory_question(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "你好",
        "今天天气怎么样",
        "你叫什么名字？",
        "帮我写个函数",
        "我觉得很累",
    ],
)
def test_ignores_non_memory_questions(text: str) -> None:
    assert is_memory_question(text) is False


def test_empty_history_reply_is_honest() -> None:
    reply = empty_history_reply()
    assert "刚开始" in reply
    assert "还没" in reply


def test_guard_triggers_on_empty_history() -> None:
    """空历史问记忆，必须走兜底，不能调用模型。"""
    card = CharacterCard.from_file(CARD_PATH)
    client = FakeClient()
    session = ConversationSession(card, client, time_aware=False)

    record = asyncio.run(session.send("你还记得我刚才在做什么吗？"))

    assert client.calls == []  # 没有调用云端
    assert record.guarded is True
    assert "刚开始" in record.assistant_text


def test_guard_does_not_trigger_with_history() -> None:
    """有真实历史时必须交给模型正常回答，不能误拦截。"""
    card = CharacterCard.from_file(CARD_PATH)
    client = FakeClient(reply="记得，你在弄 ESP32。")
    session = ConversationSession(card, client, time_aware=False)

    async def go():
        await session.send("我在弄一块 ESP32 的板子。")
        return await session.send("你还记得我刚才在做什么吗？")

    record = asyncio.run(go())
    assert record.guarded is False
    assert len(client.calls) == 2
    assert record.assistant_text == "记得，你在弄 ESP32。"


def test_guard_does_not_trigger_on_normal_first_message() -> None:
    card = CharacterCard.from_file(CARD_PATH)
    client = FakeClient()
    session = ConversationSession(card, client, time_aware=False)

    record = asyncio.run(session.send("你好，今天有点累。"))

    assert record.guarded is False
    assert len(client.calls) == 1


def test_guarded_turn_enters_history() -> None:
    """兜底回答也要进历史，否则下一轮模型不知道自己说过这句。"""
    card = CharacterCard.from_file(CARD_PATH)
    client = FakeClient(reply="第二轮回合")
    session = ConversationSession(card, client, time_aware=False)

    async def go():
        await session.send("你还记得我刚才在做什么吗？")
        await session.send("那我说说，我在弄 ESP32。")

    asyncio.run(go())
    msgs = client.calls[-1]["messages"]
    # system + 兜底那轮的 user/assistant + 本轮 user
    assert len(msgs) == 4
    assert "刚开始" in msgs[2].content
