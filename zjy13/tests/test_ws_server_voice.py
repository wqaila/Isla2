"""WebSocket 服务端的角色与音色绑定测试。

背景（盘查发现的真实缺陷，文档此前未记录）：
1. ``synthesize()`` 没有传 ``voice_id``，开发板走 WS 链路时用的是 TTSConfig
   的默认音色，而 ``dialogue_loop`` 用的是角色卡绑定的音色 ——
   结果"终端是爱莉希雅、开发板是另一个声音"。
2. ``--character-file`` 默认硬编码 ``default.yaml``（小满），
   与 ``.env`` 里的 ``DEFAULT_CHARACTER_ID=elysia`` 不一致。

这两个问题都只在"跨入口对比"时才会暴露，所以必须由测试锁住。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from server.dialogue.character import CharacterCard
from server.tts import TTSConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = PROJECT_ROOT / "server" / "ws" / "server.py"
CHAR_DIR = PROJECT_ROOT / "server" / "characters"


# ----------------------------------------------------------------------
# 音色绑定
# ----------------------------------------------------------------------


def test_synthesize_receives_voice_id() -> None:
    """WS 服务端调用 TTS 时必须显式传 voice_id。

    注意：2026-11-12 起服务端改用流式接口 ``synthesize_stream``，
    因此这里两种调用都接受，只要传了 voice_id 即可。
    """
    src = SERVER_PY.read_text(encoding="utf-8")
    match = (
        re.search(r"self\.tts\.synthesize_stream\((.*?)\)", src, re.S)
        or re.search(r"self\.tts\.synthesize\((.*?)\)", src, re.S)
    )
    assert match, "未找到 TTS 合成调用（synthesize 或 synthesize_stream）"
    call = match.group(1)
    assert "voice_id" in call, (
        "合成调用没有传 voice_id，开发板会听到错误的音色"
    )


def test_streaming_is_used() -> None:
    """服务端应走流式合成，否则首字延迟受整段合成时间拖累。

    背景：非流式要等整段音频合成完（8 秒音频约 4.8 秒）才下发，
    这段静默占端到端延迟约 80%。改用流式后首块约 1~1.5 秒即可开播。
    """
    src = SERVER_PY.read_text(encoding="utf-8")
    assert "synthesize_stream" in src, (
        "服务端没有使用流式合成，首字延迟仍受整段合成时间拖累"
    )


def test_server_resolves_voice_from_card() -> None:
    """音色应优先取角色卡绑定值。"""
    src = SERVER_PY.read_text(encoding="utf-8")
    assert "tts_settings()" in src, "没有从角色卡读取 tts_voice_id"
    assert "self.voice_id" in src


def test_voice_falls_back_to_tts_config() -> None:
    """角色卡没绑定音色时回退到 TTSConfig 默认值，不能报错。"""
    src = SERVER_PY.read_text(encoding="utf-8")
    assert "default_voice" in src


# ----------------------------------------------------------------------
# 角色卡默认值
# ----------------------------------------------------------------------


def test_character_file_not_hardcoded() -> None:
    """--character-file 不能硬编码 default.yaml。"""
    src = SERVER_PY.read_text(encoding="utf-8")
    assert "DEFAULT_CHARACTER_ID" in src, (
        "WS 服务端没有读取 DEFAULT_CHARACTER_ID，会与 .env 不一致"
    )
    # 硬编码形式：default=str(root / "server" / "characters" / "default.yaml")
    assert 'default=str(root / "server" / "characters" / "default.yaml")' not in src


def test_character_selection_after_env_load() -> None:
    """角色选择必须在 load_env_file 之后，否则读到旧环境变量。

    踩过的坑：argparse 的 default 在解析时就求值，
    那时 .env 还没加载，于是永远拿到 "default"。
    """
    src = SERVER_PY.read_text(encoding="utf-8")
    env_pos = src.index("load_env_file(")
    char_pos = src.index("DEFAULT_CHARACTER_ID")
    assert char_pos > env_pos, "角色选择发生在 .env 加载之前"


# ----------------------------------------------------------------------
# 行为验证（不依赖真实网络）
# ----------------------------------------------------------------------


class _FakeTTS:
    """最小 TTS 替身，只用于验证 voice_id 传递。"""

    def __init__(self, default_voice: str = "elysia") -> None:
        self.config = TTSConfig(default_voice=default_voice)
        self.calls: list[dict] = []


def test_dialogue_server_picks_card_voice() -> None:
    """DialogueServer 应优先使用角色卡绑定的音色。"""
    from server.ws.server import DialogueServer, ServerConfig

    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    fake_tts = _FakeTTS(default_voice="elysia")

    server = DialogueServer(
        ServerConfig(),
        llm_client=None,  # 未使用
        tts_client=fake_tts,
        card=card,
    )
    assert server.voice_id == "elysia"


def test_dialogue_server_falls_back_when_card_unbound() -> None:
    """角色卡未绑定音色时回退到 TTSConfig 默认值。"""
    from server.ws.server import DialogueServer, ServerConfig

    payload = {
        "character_id": "nobind",
        "name": "无绑定角色",
        "identity": "测试",
    }
    card = CharacterCard.from_dict(payload)
    fake_tts = _FakeTTS(default_voice="fallback_voice")

    server = DialogueServer(
        ServerConfig(),
        llm_client=None,
        tts_client=fake_tts,
        card=card,
    )
    assert server.voice_id == "fallback_voice"


def test_dialogue_server_explicit_voice_wins() -> None:
    """显式传入的 voice_id 优先级最高。"""
    from server.ws.server import DialogueServer, ServerConfig

    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    fake_tts = _FakeTTS(default_voice="elysia")

    server = DialogueServer(
        ServerConfig(),
        llm_client=None,
        tts_client=fake_tts,
        card=card,
        voice_id="override",
    )
    assert server.voice_id == "override"
