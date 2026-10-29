"""音色注册与角色卡绑定的测试。

背景（真实踩坑）：
CosyVoice3 的参考文本必须带 ``<|endofprompt|>`` 前缀分隔符。
只写纯文本时，音色注册会"成功"、健康检查也正常，
但每次合成都在卷积层报
    RuntimeError: Calculated padded input size per channel: (3).
    Kernel size: (4). Kernel size can't be greater than actual input size
这类"注册成功但一用就崩"的问题必须由测试锁住。
"""

from __future__ import annotations

import re
import wave
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHAR_DIR = PROJECT_ROOT / "server" / "characters"
VOICE_DIR = CHAR_DIR / "voices"
SERVER_SCRIPT = PROJECT_ROOT / "scripts" / "tts_server_wsl.py"


# ----------------------------------------------------------------------
# 角色卡
# ----------------------------------------------------------------------


def test_elysia_card_loads() -> None:
    from server.dialogue.character import CharacterCard

    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    assert card.character_id == "elysia"
    assert card.name == "爱莉希雅"
    assert len(card.test_dialogues()) >= 5


def test_elysia_card_binds_voice() -> None:
    """角色卡必须绑定音色，否则切换角色时声音不会跟着走。"""
    from server.dialogue.character import CharacterCard

    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    settings = card.tts_settings()
    assert settings["tts_voice_id"] == "elysia"
    assert settings["tts_provider"] == "cosyvoice3"


def test_elysia_prompt_has_no_placeholder() -> None:
    from server.dialogue.character import CharacterCard

    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    prompt = card.system_prompt()
    assert "未设定" not in prompt
    assert "爱莉希雅" in prompt
    # 称呼是"舰长"，这是角色卡的核心设定
    assert "舰长" in prompt
    # 安全规则必须保留
    assert "不得编造" in prompt


def test_elysia_prompt_carries_length_limit() -> None:
    """原 JSON 角色卡有明确的字数上限，迁移后不能丢。"""
    from server.dialogue.character import CharacterCard

    card = CharacterCard.from_file(CHAR_DIR / "elysia.yaml")
    prompt = card.system_prompt()
    assert "200" in prompt


def test_character_card_accepts_json(tmp_path: Path) -> None:
    """JSON 角色卡要能直接加载（zjy9 的 elysia.json 就是 JSON）。"""
    import json

    from server.dialogue.character import CharacterCard

    payload = {"character_id": "t", "name": "测试角色", "identity": "测试"}
    p = tmp_path / "card.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    card = CharacterCard.from_file(p)
    assert card.character_id == "t"
    assert card.name == "测试角色"


def test_character_card_rejects_bad_json(tmp_path: Path) -> None:
    from server.dialogue.character import CharacterCard
    from server.llm.base import LLMConfigError

    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(LLMConfigError) as exc:
        CharacterCard.from_file(p)
    assert "JSON" in str(exc.value)


# ----------------------------------------------------------------------
# 参考音频
# ----------------------------------------------------------------------


def test_elysia_reference_audio_exists() -> None:
    wav = VOICE_DIR / "elysia" / "ref_elysia.wav"
    assert wav.is_file(), f"参考音频缺失: {wav}"


def test_elysia_reference_audio_format() -> None:
    """参考音频必须是 24kHz 单声道 16bit —— CosyVoice3 原生格式。"""
    wav = VOICE_DIR / "elysia" / "ref_elysia.wav"
    with wave.open(str(wav), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 24000
        duration = w.getnframes() / w.getframerate()

    # 3~12 秒：太短学不到音色，太长拖慢编码
    assert 3.0 <= duration <= 12.0, f"参考音频时长 {duration:.2f}s 超出合理范围"


def test_elysia_reference_audio_not_clipped() -> None:
    wav = VOICE_DIR / "elysia" / "ref_elysia.wav"
    with wave.open(str(wav), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    peak = float(np.abs(data).max())
    # 归一化到 -3dBFS 左右，留足余量
    assert peak < 32767 * 0.95, f"参考音频接近削波: peak={peak}"
    assert peak > 32767 * 0.3, f"参考音频电平过低: peak={peak}"


def test_elysia_reference_text_exists() -> None:
    txt = VOICE_DIR / "elysia" / "ref_elysia.txt"
    assert txt.is_file()
    content = txt.read_text(encoding="utf-8").strip()
    assert len(content) >= 10, "参考文本太短，零样本克隆需要足够的文本"


# ----------------------------------------------------------------------
# 服务端：endofprompt 前缀（关键回归测试）
# ----------------------------------------------------------------------


def test_server_script_defines_prompt_prefix() -> None:
    """服务端必须定义并应用 <|endofprompt|> 前缀。

    这是实测踩到的坑：缺前缀时音色能注册成功，但合成必然崩。
    """
    src = SERVER_SCRIPT.read_text(encoding="utf-8")
    assert "<|endofprompt|>" in src
    assert "PROMPT_PREFIX" in src


def test_server_script_applies_prefix_to_discovered_voices() -> None:
    """扫描到的音色必须自动补前缀，不能依赖人工把前缀写进 .txt。"""
    src = SERVER_SCRIPT.read_text(encoding="utf-8")
    # 找到 discover_voices 函数体
    match = re.search(r"def discover_voices\(\).*?(?=\n@dataclass|\ndef |\nclass )", src, re.S)
    assert match, "未找到 discover_voices 函数"
    body = match.group(0)
    assert "PROMPT_PREFIX" in body, "discover_voices 没有补 endofprompt 前缀"


def test_reference_text_file_has_no_prefix() -> None:
    """磁盘上的参考文本保持纯文本，前缀由服务端统一补。

    这样人工编辑 .txt 时不用记特殊标记，也避免重复添加前缀。
    """
    txt = VOICE_DIR / "elysia" / "ref_elysia.txt"
    content = txt.read_text(encoding="utf-8")
    assert "<|endofprompt|>" not in content


def test_voice_profile_registered_by_id() -> None:
    """音色按 id 注册到 spk2info，天然支持多音色共存。"""
    src = SERVER_SCRIPT.read_text(encoding="utf-8")
    assert "add_zero_shot_spk" in src
    # 必须用循环注册，而不是硬编码单个音色
    assert "for voice_id, prof in runtime.voices.items()" in src
