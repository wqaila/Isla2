"""会话层测试：历史维护、失败不入历史、取消、统计。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from server.dialogue.character import CharacterCard
from server.dialogue.session import ConversationSession
from server.llm.base import LLMError, LLMResult

CARD_PATH = Path(__file__).resolve().parent.parent / "server" / "characters" / "default.yaml"


class FakeClient:
    """记录调用参数的假客户端，不发起网络请求。"""

    def __init__(self, reply: str = "好的", fail_times: int = 0) -> None:
        self.reply = reply
        self.fail_times = fail_times
        self.calls: list[dict] = []
        self.cancelled = 0

    async def chat(self, messages, *, turn_id=None, on_delta=None, **kw):
        self.calls.append({"messages": list(messages), "turn_id": turn_id})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise LLMError("模拟失败")
        if on_delta:
            on_delta(self.reply)
        return LLMResult(
            text=self.reply,
            model="fake",
            finish_reason="stop",
            first_token_ms=100.0,
            total_ms=200.0,
        )

    async def cancel(self) -> bool:
        self.cancelled += 1
        return True


@pytest.fixture()
def card() -> CharacterCard:
    return CharacterCard.from_file(CARD_PATH)


def test_first_turn_has_system_and_user(card) -> None:
    client = FakeClient()
    session = ConversationSession(card, client, time_aware=False)

    asyncio.run(session.send("你好"))

    msgs = client.calls[0]["messages"]
    assert [m.role for m in msgs] == ["system", "user"]
    assert msgs[-1].content == "你好"


def test_time_context_injected_into_user_message(card) -> None:
    """时间必须进 user 消息，不能进 system，否则破坏 prompt 缓存前缀。"""
    client = FakeClient()
    session = ConversationSession(card, client, time_aware=True)

    asyncio.run(session.send("你好"))

    msgs = client.calls[0]["messages"]
    assert "系统时间" in msgs[-1].content
    assert "你好" in msgs[-1].content
    assert "系统时间" not in msgs[0].content  # system 保持稳定


def test_time_context_clarifies_source(card) -> None:
    """必须说明时间是服务端给的，否则模型会说"以你的设备为准"推卸责任。"""
    from datetime import datetime

    from server.dialogue.character import build_messages

    card_obj = CharacterCard.from_file(CARD_PATH)
    msgs = build_messages(card_obj, [], "现在几点", now=datetime(2026, 10, 23, 9, 40))
    content = msgs[-1].content
    assert "服务端" in content
    assert "不要声称这是用户的设备时间" in content


def test_time_aware_disabled(card) -> None:
    client = FakeClient()
    session = ConversationSession(card, client, time_aware=False)

    asyncio.run(session.send("你好"))

    assert "现在是" not in client.calls[0]["messages"][-1].content


def test_history_accumulates(card) -> None:
    client = FakeClient()
    session = ConversationSession(card, client, time_aware=False)

    async def go():
        await session.send("第一句")
        await session.send("第二句")

    asyncio.run(go())
    msgs = client.calls[1]["messages"]
    assert [m.role for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1].content == "第一句"
    assert msgs[2].content == "好的"
    assert msgs[3].content == "第二句"


def test_turn_ids_increment(card) -> None:
    client = FakeClient()
    session = ConversationSession(card, client, session_id="abc")

    async def go():
        r1 = await session.send("一")
        r2 = await session.send("二")
        return r1, r2

    r1, r2 = asyncio.run(go())
    assert r1.turn_id == "abc-t1"
    assert r2.turn_id == "abc-t2"


def test_failed_turn_not_in_history(card) -> None:
    """失败轮次不能污染上下文，否则模型会把错误回复当成自己说过的话。"""
    client = FakeClient(fail_times=1)
    session = ConversationSession(card, client, time_aware=False)

    async def go():
        with pytest.raises(LLMError):
            await session.send("会失败的")
        await session.send("这句正常")

    asyncio.run(go())
    msgs = client.calls[-1]["messages"]
    assert [m.role for m in msgs] == ["system", "user"]
    assert msgs[-1].content == "这句正常"
    assert len(session.turns) == 2
    assert session.turns[0].ok is False
    assert session.turns[1].ok is True


def test_reset_clears_history(card) -> None:
    client = FakeClient()
    session = ConversationSession(card, client, time_aware=False)

    async def go():
        await session.send("一")
        session.reset()
        await session.send("二")

    asyncio.run(go())
    msgs = client.calls[-1]["messages"]
    assert [m.role for m in msgs] == ["system", "user"]


def test_reset_changes_session_id_and_turn_id(card) -> None:
    """清空对话后 turn_id 必须变化，否则会被防重复机制拦截（实测 bug）。"""
    client = FakeClient()
    session = ConversationSession(card, client, time_aware=False)

    async def go():
        await session.send("一")
        old_sid = session.session_id
        old_turn = client.calls[-1]["turn_id"]
        session.reset()
        await session.send("二")
        return old_sid, old_turn, session.session_id, client.calls[-1]["turn_id"]

    old_sid, old_turn, new_sid, new_turn = asyncio.run(go())
    assert new_sid != old_sid
    assert new_turn != old_turn
    assert new_turn.startswith(new_sid)


def test_empty_input_rejected(card) -> None:
    session = ConversationSession(card, FakeClient())
    with pytest.raises(ValueError):
        asyncio.run(session.send("   "))


def test_stats_shape(card) -> None:
    client = FakeClient()
    session = ConversationSession(card, client)

    async def go():
        await session.send("一")
        return session.stats()

    stats = asyncio.run(go())
    assert stats["turns_ok"] == 1
    assert stats["turns_failed"] == 0
    assert stats["first_token_ms_mean"] == 100.0
    assert stats["history_messages"] == 2


def test_history_limit_applied(card) -> None:
    client = FakeClient()
    session = ConversationSession(card, client, max_history_messages=2, time_aware=False)

    async def go():
        for i in range(4):
            await session.send(f"第{i}句")

    asyncio.run(go())
    msgs = client.calls[-1]["messages"]
    # system + 最多 2 条历史 + 当前输入
    assert len(msgs) <= 1 + 2 + 1


def test_examples_injected_into_system(card) -> None:
    """示例对话必须进入 system 前缀，才能既锚定风格又命中缓存。"""
    client = FakeClient()
    session = ConversationSession(card, client, include_examples=True, time_aware=False)

    asyncio.run(session.send("你好"))

    system = client.calls[0]["messages"][0].content
    assert "对话示例" in system
    assert "用户：" in system


def test_examples_can_be_disabled(card) -> None:
    client = FakeClient()
    session = ConversationSession(card, client, include_examples=False, time_aware=False)

    asyncio.run(session.send("你好"))

    system = client.calls[0]["messages"][0].content
    assert "对话示例" not in system


def test_truncation_applied_on_length_finish(card) -> None:
    """finish_reason=length 时必须在句边界收尾，不能留半句。"""

    class TruncatingClient(FakeClient):
        async def chat(self, messages, *, turn_id=None, on_delta=None, **kw):
            self.calls.append({"messages": list(messages), "turn_id": turn_id})
            return LLMResult(
                text="第一句。第二句。第三句。第四句。第五句。第六句。",
                model="fake",
                finish_reason="length",
                first_token_ms=10.0,
                total_ms=20.0,
            )

    session = ConversationSession(card, TruncatingClient(), max_sentences=2, time_aware=False)

    async def go():
        return await session.send("你好")

    record = asyncio.run(go())
    assert record.truncated is True
    assert record.assistant_text == "第一句。第二句。"


def test_no_truncation_on_normal_finish(card) -> None:
    client = FakeClient(reply="第一句。第二句。第三句。第四句。第五句。第六句。")
    session = ConversationSession(card, client, max_sentences=2, time_aware=False)

    async def go():
        return await session.send("你好")

    record = asyncio.run(go())
    assert record.truncated is False
    assert record.assistant_text.count("。") == 6


def test_new_turn_cancels_previous(card) -> None:
    client = FakeClient()
    session = ConversationSession(card, client)

    async def go():
        await session.send("一")
        session._active_turn_id = "stale"  # 模拟上一轮仍在进行
        await session.send("二")

    asyncio.run(go())
    assert client.cancelled == 1
