"""对话前置检查：识别"空历史 + 询问记忆"这类高风险诱导问题。

背景（实测缺陷）：
全新会话中用户问「你还记得我刚才在做什么吗？」，模型有约 1/3 概率**编造**一个
并不存在的"刚才"（如"你刚才在整理那份会议记录"）。这类问题的问句本身预设了
"刚才发生过事"，光靠提示词里写"禁止编造"压不住。

因此这里做**结构性拦截**：当会话确实没有任何历史、而用户又在问记忆类问题时，
直接给出确定的诚实回答，不交给模型自由发挥。这比调提示词可靠得多。
"""

from __future__ import annotations

import re

# 记忆类提问的特征词：询问"刚才/之前/记得/说过什么"
_MEMORY_PATTERNS = [
    re.compile(r"记不记得"),
    re.compile(r"还记得"),
    re.compile(r"记得.*(吗|么|嘛)"),
    re.compile(r"(刚才|刚刚|之前|上次|前面|上回).{0,6}(说|聊|做|提|讲|问)"),
    re.compile(r"(说|聊|做|提|讲|问)过.{0,4}(什么|啥|哪些)"),
    re.compile(r"我们.{0,4}(聊|说).{0,4}(什么|啥)"),
    re.compile(r"我(叫|是).{0,3}(什么|啥|谁)"),
    re.compile(r"我(的)?名字"),
]

# 排除：明显在问外部事实或自我介绍，不是问会话记忆
_NOT_MEMORY = [
    re.compile(r"^(你好|您好|hi|hello)"),
    re.compile(r"你(叫|是)(什么|谁)"),
]


def is_memory_question(text: str) -> bool:
    """判断用户是否在询问本次会话的历史内容。"""
    text = (text or "").strip()
    if not text:
        return False
    for pattern in _NOT_MEMORY:
        if pattern.search(text):
            return False
    return any(p.search(text) for p in _MEMORY_PATTERNS)


def empty_history_reply(card_name: str = "") -> str:
    """空历史时的确定回答。

    明确说明"刚开始聊、你还没说过"，不编造任何内容。
    """
    return (
        "我们刚开始聊，你还没跟我说过你在做什么。"
        "你要是想让我记住点什么，现在告诉我就行。"
    )


__all__ = ["is_memory_question", "empty_history_reply"]
