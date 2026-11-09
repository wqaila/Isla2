"""角色卡 TTS 参数是否真正生效的测试。

背景（``docs/character-review.md`` 缺陷 4）：
> 文档第七节第 16 条要求：角色卡里 TTS 不支持的参数必须明确提示或忽略，
> 不能声称已生效。当前只是透传，未做校验——等接 TTS 时必须补。

TTS 早就接上了，但 ``speaking_rate`` 一直只是被 ``tts_settings()`` 读出来、
**从未传给 ``synthesize()``**。本文件把这条链路锁住。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.dialogue.character import CharacterCard
from server.tts import TTSConfig
from server.ws.server import DialogueServer, ServerConfig

CHAR_DIR = Path(__file__).resolve().parent.parent / "server" / "characters"
SERVER_PY = Path(__file__).resolve().parent.parent / "server" / "ws" / "server.py"
LOOP_PY = Path(__file__).resolve().parent.parent / "tools" / "dialogue_loop.py"


class _FakeTTS:
    def __init__(self) -> None:
        self.config = TTSConfig()


# ----------------------------------------------------------------------
# 角色卡字段
# ----------------------------------------------------------------------


def test_elysia_card_declares_speaking_rate() -> None:
    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    settings = card.tts_settings()
    assert settings["speaking_rate"] == 1.0
    assert settings["tts_voice_id"] == "elysia"


def test_tts_settings_exposes_required_keys() -> None:
    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    settings = card.tts_settings()
    for key in ("tts_provider", "tts_voice_id", "speaking_rate", "pitch_style"):
        assert key in settings


# ----------------------------------------------------------------------
# 服务端把 speaking_rate 真的传给 TTS
# ----------------------------------------------------------------------


def test_server_reads_speaking_rate_from_card() -> None:
    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    server = DialogueServer(
        ServerConfig(),
        llm_client=None,
        tts_client=_FakeTTS(),
        card=card,
    )
    assert server.speaking_rate == 1.0


def test_server_defaults_to_1_when_card_omits_rate() -> None:
    card = CharacterCard.from_dict(
        {"character_id": "x", "name": "无语速角色", "identity": "测试"}
    )
    server = DialogueServer(
        ServerConfig(), llm_client=None, tts_client=_FakeTTS(), card=card
    )
    assert server.speaking_rate == 1.0


def test_server_passes_speed_to_synthesize() -> None:
    """``synthesize`` 调用必须同时带 voice_id 与 speed。"""
    src = SERVER_PY.read_text(encoding="utf-8")
    idx = src.index("self.tts.synthesize(")
    call = src[idx:idx + 320]
    assert "voice_id" in call, "未传 voice_id"
    assert "speed" in call, "未传 speed，角色卡的 speaking_rate 不会生效"


def test_dialogue_loop_passes_speed_to_synthesize() -> None:
    src = LOOP_PY.read_text(encoding="utf-8")
    idx = src.index("tts_client.synthesize(")
    call = src[idx:idx + 320]
    assert "speed" in call, "终端闭环未传 speed"


def test_dialogue_loop_reads_speaking_rate_from_card() -> None:
    src = LOOP_PY.read_text(encoding="utf-8")
    assert "speaking_rate" in src
    assert "tts_settings()" in src
