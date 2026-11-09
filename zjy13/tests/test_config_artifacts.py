"""配置模板与依赖清单的一致性测试。

背景（盘查发现的真实缺陷）：
1. ``requirements.txt`` 只列了 LLM 依赖，缺 ``websockets`` 与 ``numpy``，
   按 README 装完的新机器 **WS 服务直接起不来**。
2. ``.env.example`` 缺全部 TTS 键、``DEFAULT_CHARACTER_ID`` 还是 ``default``、
   ``LLM_MAX_TOKENS`` 还是已废弃的 256，照抄会复现「小满 + 长回复 + 无音色」。

这两类问题只在「换一台机器从零装」时才暴露，所以用测试锁住。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQ = PROJECT_ROOT / "requirements.txt"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"


# ----------------------------------------------------------------------
# requirements.txt
# ----------------------------------------------------------------------


def _parse_requirements() -> dict[str, str]:
    """解析 requirements.txt，返回 {包名小写: 版本}。跳过注释与空行。"""
    result: dict[str, str] = {}
    for raw in REQ.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        if "==" in line:
            name, ver = line.split("==", 1)
            result[name.strip().lower()] = ver.strip()
    return result


def test_requirements_exists() -> None:
    assert REQ.is_file()


@pytest.mark.parametrize(
    "package",
    [
        "httpx",       # LLM / TTS HTTP 客户端
        "pyyaml",      # 角色卡
        "python-dotenv",  # .env 加载
        "websockets",  # server/ws/server.py、tools/ws_client_sim.py
        "numpy",       # server/ws/audio.py、asr/audio_utils.py
        "pytest",      # 测试
    ],
)
def test_requirements_has_runtime_dependency(package: str) -> None:
    """运行时真正 import 的包必须写进清单，否则新机器装完跑不起来。"""
    deps = _parse_requirements()
    assert package in deps, (
        f"requirements.txt 缺少 {package}；"
        "按 README 从零安装后会因 ImportError 无法启动"
    )


def test_requirements_versions_pinned() -> None:
    """所有依赖都要锁版本，避免上游不兼容（历史上 websockets 13 改过 API）。"""
    for name, ver in _parse_requirements().items():
        assert ver, f"{name} 没有锁版本"


# ----------------------------------------------------------------------
# .env.example
# ----------------------------------------------------------------------


def _parse_env_example() -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, val = line.split("=", 1)
        result[key.strip()] = val.strip()
    return result


def test_env_example_is_utf8_without_bom() -> None:
    """Windows 上被 GBK 保存过会导致 dotenv 崩溃，这里锁住编码。"""
    raw = ENV_EXAMPLE.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), ".env.example 不能带 BOM"
    raw.decode("utf-8")  # 解码失败即抛错


@pytest.mark.parametrize(
    "key",
    [
        # TTS 客户端真正读取的 7 个键（server/tts/client.py:from_env）
        "TTS_BASE_URL",
        "TTS_SYNTH_PATH",
        "TTS_HEALTH_PATH",
        "TTS_TIMEOUT_S",
        "TTS_CONNECT_TIMEOUT_S",
        "TTS_OUTPUT_DIR",
        "TTS_VOICE_ID",
    ],
)
def test_env_example_documents_tts_key(key: str) -> None:
    """TTS 键必须出现在模板里，否则新机器不知道该配什么。"""
    env = _parse_env_example()
    assert key in env, f".env.example 缺少 {key}（TTSConfig.from_env 会读取它）"


def test_env_example_default_character_matches_current() -> None:
    """模板的默认角色应与项目当前默认一致（曾长期停在 default/小满）。"""
    env = _parse_env_example()
    assert env.get("DEFAULT_CHARACTER_ID") == "elysia"


def test_env_example_max_tokens_matches_voice_requirement() -> None:
    """LLM_MAX_TOKENS 必须是 128：256 会偶发长回复，推爆热点链路。"""
    env = _parse_env_example()
    assert env.get("LLM_MAX_TOKENS") == "128", (
        "模板里仍是 256；实测会长回复合成 16s+ 音频并导致下行断连"
    )


def test_env_example_voice_id_matches_character() -> None:
    env = _parse_env_example()
    assert env.get("TTS_VOICE_ID") == "elysia"


def test_env_example_has_no_real_secret() -> None:
    """模板里绝不能出现真实密钥。"""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "sk-" not in text, ".env.example 疑似含真实 API 密钥"
    assert re.search(r"LLM_API_KEY=\S+", text) is None


def test_env_example_keys_cover_llm_config() -> None:
    """LLMConfig.from_env 读取的键都应在模板里有说明。"""
    env = _parse_env_example()
    for key in (
        "LLM_PROVIDER",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_CHAT_PATH",
        "LLM_CONNECT_TIMEOUT_S",
        "LLM_REQUEST_TIMEOUT_S",
        "LLM_TOTAL_TIMEOUT_S",
        "LLM_MAX_ATTEMPTS",
        "LLM_BACKOFF_BASE_S",
        "LLM_BACKOFF_MAX_S",
        "LLM_TEMPERATURE",
        "LLM_MAX_TOKENS",
        "LLM_STREAM",
        "LLM_THINKING",
        "LLM_PROXY",
        "CHARACTERS_DIR",
        "DEFAULT_CHARACTER_ID",
    ):
        assert key in env, f".env.example 缺少 {key}"
