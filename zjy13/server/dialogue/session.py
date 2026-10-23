"""会话层：管理多轮对话的历史、轮次编号、取消与计时。

职责边界（对应文档第八、十二节）：
- 维护 ``session_id`` 与每轮 ``turn_id``，保证上一轮的延迟回复不会覆盖新一轮。
- 维护最近对话历史，并在发送前按条数裁剪。
- 记录每轮的首字延迟与总耗时。
- 不负责 ASR / TTS / WebSocket —— 那是后续阶段。

失败轮次不会写入历史，避免把错误回复当成上下文继续喂给模型。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from ..llm.base import ChatMessage, LLMError
from .character import CharacterCard, build_messages
from .guards import empty_history_reply, is_memory_question
from .text_utils import DEFAULT_MAX_SENTENCES, soft_truncate

DeltaCallback = Callable[[str], None]


@dataclass(slots=True)
class TurnRecord:
    """一轮对话的记录。失败时 ``error`` 非空且不计入历史。"""

    turn_id: str
    index: int
    user_text: str
    assistant_text: str = ""
    first_token_ms: float | None = None
    total_ms: float = 0.0
    error: str | None = None
    truncated: bool = False
    guarded: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.assistant_text.strip())

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "index": self.index,
            "user": self.user_text,
            "assistant": self.assistant_text,
            "first_token_ms": round(self.first_token_ms, 1) if self.first_token_ms else None,
            "total_ms": round(self.total_ms, 1),
            "error": self.error,
            "truncated": self.truncated,
            "guarded": self.guarded,
        }


class ConversationSession:
    """一个持续的对话会话。

    用法::

        session = ConversationSession(card, client)
        record = await session.send("你好", on_delta=print)
    """

    def __init__(
        self,
        card: CharacterCard,
        client,
        *,
        max_history_messages: int = 20,
        session_id: str | None = None,
        max_sentences: int = DEFAULT_MAX_SENTENCES,
        include_examples: bool = True,
        time_aware: bool = True,
    ) -> None:
        self.card = card
        self.client = client
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.max_history_messages = max_history_messages
        self.max_sentences = max_sentences
        self.include_examples = include_examples
        self.time_aware = time_aware
        self._turns: list[TurnRecord] = []
        self._active_turn_id: str | None = None

    # ------------------------------------------------------------------

    @property
    def turns(self) -> list[TurnRecord]:
        return list(self._turns)

    @property
    def is_busy(self) -> bool:
        return self._active_turn_id is not None

    @property
    def active_turn_id(self) -> str | None:
        return self._active_turn_id

    def history_messages(self) -> list[ChatMessage]:
        """把成功的轮次还原成消息列表，供下一轮拼装上下文。"""
        messages: list[ChatMessage] = []
        for turn in self._turns:
            if turn.ok:
                messages.append(ChatMessage("user", turn.user_text))
                messages.append(ChatMessage("assistant", turn.assistant_text))
        return messages

    def reset(self) -> None:
        """清空会话历史，并换一个新的会话标识。

        注意：turn_id 由 ``session_id + 轮次序号`` 组成，若只清空 ``_turns``
        而不换 session_id，下一轮的 turn_id 会与清空前重复，
        被客户端的防重复扣费机制拦截（实测报 duplicate_turn）。
        因此这里必须重新生成 session_id。
        """
        self._turns.clear()
        self._active_turn_id = None
        self.session_id = uuid.uuid4().hex[:12]

    async def cancel(self) -> bool:
        """取消当前进行中的轮次。"""
        return await self.client.cancel()

    def stats(self) -> dict:
        ok_turns = [t for t in self._turns if t.ok]
        firsts = [t.first_token_ms for t in ok_turns if t.first_token_ms is not None]
        totals = [t.total_ms for t in ok_turns if t.total_ms]
        return {
            "session_id": self.session_id,
            "character": self.card.name,
            "turns_total": len(self._turns),
            "turns_ok": len(ok_turns),
            "turns_failed": len(self._turns) - len(ok_turns),
            "first_token_ms_mean": round(sum(firsts) / len(firsts), 1) if firsts else None,
            "total_ms_mean": round(sum(totals) / len(totals), 1) if totals else None,
            "history_messages": len(self.history_messages()),
        }

    # ------------------------------------------------------------------

    async def send(
        self,
        text: str,
        *,
        on_delta: DeltaCallback | None = None,
    ) -> TurnRecord:
        """发送一轮用户输入，返回该轮记录。

        若上一轮仍在进行，会先取消它，防止旧回复覆盖新内容。

        :raises LLMError: 调用失败时抛出，同时把失败记录保留在 ``turns`` 中
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("输入为空")

        if self._active_turn_id is not None:
            await self.cancel()

        turn_id = f"{self.session_id}-t{len(self._turns) + 1}"
        self._active_turn_id = turn_id
        record = TurnRecord(turn_id=turn_id, index=len(self._turns) + 1, user_text=text)

        started = time.perf_counter()
        try:
            history = self.history_messages()

            # 结构性拦截：空历史 + 询问记忆，直接给确定回答。
            # 实测模型在此场景约 1/3 概率编造"你刚才在做什么"，
            # 提示词压不住，必须在代码层兜住。
            if not history and is_memory_question(text):
                reply = empty_history_reply(self.card.name)
                record.assistant_text = reply
                record.total_ms = (time.perf_counter() - started) * 1000
                record.first_token_ms = record.total_ms
                record.guarded = True
                if on_delta is not None:
                    on_delta(reply)
                self._turns.append(record)
                return record

            messages = build_messages(
                self.card,
                history,
                text,
                max_history_messages=self.max_history_messages,
                include_examples=self.include_examples,
                now=datetime.now() if self.time_aware else None,
            )
            result = await self.client.chat(messages, turn_id=turn_id, on_delta=on_delta)
            text = result.text
            # 只有被 token 上限硬截断时才做句边界修复，正常结束不动原文
            if result.finish_reason == "length":
                text, truncated = soft_truncate(
                    text, max_sentences=self.max_sentences
                )
                if truncated:
                    record.truncated = True
            record.assistant_text = text
            record.first_token_ms = result.first_token_ms
            record.total_ms = result.total_ms
            self._turns.append(record)
            return record
        except LLMError as exc:
            record.error = str(exc)
            record.total_ms = (time.perf_counter() - started) * 1000
            self._turns.append(record)
            raise
        finally:
            if self._active_turn_id == turn_id:
                self._active_turn_id = None


__all__ = ["ConversationSession", "TurnRecord"]
