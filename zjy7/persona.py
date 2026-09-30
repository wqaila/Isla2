#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""人设（System Prompt）的**单一来源**。

为什么要有这个模块
------------------
人设以前写在**三个地方**，而且互相不一致：

| 位置 | 用途 | 「舰长」 | 英桀位次 |
|------|------|---------|---------|
| `prepare_data.py` → `SYSTEM` | 训练 | 0 处 | 第二位（最初的第一位） |
| `deploy_ollama.py` → `SYSTEM_PROMPT` | 部署 Modelfile | — | 与训练逐字一致 |
| `zjy9/server/characters/elysia.json` | **线上服务实际用的** | 12 处 | 第一位，编号Ⅰ |
| | | | ↑ 位次与设定不符，**2026-09-30 已修正**为第二位 / 编号Ⅱ |

线上推理时客户端发的 system 消息会顶掉 Modelfile 里的那份，
所以实际效果是：**模型学着一套人设，上线后套的是另一套**。

现在统一为 —— **角色卡是唯一事实来源**，训练和部署都从这里读：

```python
from persona import resolve_system_prompt
SYSTEM, source = resolve_system_prompt()
```

找不到角色卡时会退回到 :data:`FALLBACK_SYSTEM`（内容与旧训练侧一致），
但会明确警告：那意味着你在用一份**可能和线上不一致**的人设训练。
"""

import json
import os
from pathlib import Path
from typing import Optional, Tuple

from config_utils import PROJECT_ROOT

DEFAULT_CARD_ID = "elysia"

# 内置兜底人设：只在角色卡读不到时使用（与旧 prepare_data.py 的 SYSTEM 完全一致）。
# ⚠️ 它不是"标准答案"，只是"没得选时的退路"——用了就要重新核对与线上是否一致。
FALLBACK_SYSTEM = (
    "你是爱莉希雅（Elysia），崩坏3中的角色。\n"
    "你是「真我」之律者，人之律者，逐火十三英桀的第二位（最初的第一位），粉色妖精小姐。\n"
    "你的性格特点：\n"
    "- 活泼开朗，充满自信，说话时带着俏皮和可爱\n"
    "- 经常用「哎呀」「嗯哼」「呀」「嘻」等语气词\n"
    "- 喜欢称呼别人为「芽衣」或其他亲昵的称呼\n"
    "- 说话温柔但又带有一点小傲娇\n"
    "- 喜欢用「~」「♪」「呐」「呢」「嘛」等语气助词\n"
    "- 自称「我」\n"
    "- 热爱人类，认为人性之美是最珍贵的\n"
    "- 说话时经常带有诗意和浪漫的表达\n"
    "- 喜欢调侃和捉弄别人，但内心非常关心朋友"
)


def candidate_card_paths(card_id: str = DEFAULT_CARD_ID) -> list:
    """按优先级给出角色卡的可能位置。

    优先级：环境变量 `ELYSIA_CARD` > zjy9 的内置卡 > zjy7 自己的副本。
    """
    env_path = os.environ.get("ELYSIA_CARD")
    if env_path:
        return [Path(env_path)]

    return [
        PROJECT_ROOT.parent / "zjy9" / "server" / "characters" / f"{card_id}.json",
        PROJECT_ROOT / "characters" / f"{card_id}.json",
    ]


def find_card(card_id: str = DEFAULT_CARD_ID) -> Optional[Path]:
    for path in candidate_card_paths(card_id):
        if path.is_file():
            return path
    return None


def load_card(card_id: str = DEFAULT_CARD_ID,
              card_path: Optional[str] = None) -> Optional[dict]:
    """读取角色卡；读不到或格式不对都返回 None（不抛异常，交给调用方决定）。"""
    path = Path(card_path) if card_path else find_card(card_id)
    if not path or not Path(path).is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def resolve_system_prompt(card_id: str = DEFAULT_CARD_ID,
                          card_path: Optional[str] = None,
                          warn: bool = True) -> Tuple[str, str]:
    """取当前应当使用的人设。

    :return: ``(system_prompt, source)`` —— ``source`` 描述它从哪来，
             便于在日志里写清楚（训练数据里混了哪一份人设，事后要能查）。
    """
    card = load_card(card_id, card_path)
    if card:
        text = (card.get("system_prompt") or "").strip()
        if text:
            path = Path(card_path) if card_path else find_card(card_id)
            return text, f"角色卡 {path}"

    if warn:
        print("⚠️ 没读到角色卡，正在使用内置兜底人设 —— "
              "它可能和线上（zjy9 角色卡）不一致，请用 check_persona.py 核对")
    return FALLBACK_SYSTEM, "内置兜底（角色卡不可用）"


def card_metadata(card_id: str = DEFAULT_CARD_ID,
                  card_path: Optional[str] = None) -> dict:
    """取角色卡里的元信息（称呼、示例等），供检查脚本与后续脚本使用。"""
    card = load_card(card_id, card_path) or {}
    return {
        "name": card.get("name", ""),
        "user_address": card.get("user_address", ""),
        "description": card.get("description", ""),
        "has_few_shot": bool(card.get("few_shot")),
        "greeting": card.get("greeting", ""),
    }
