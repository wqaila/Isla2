"""配置层测试：环境变量解析、缺失项报告、脱敏。"""

from __future__ import annotations

import pytest

from server.config import LLMConfig
from server.llm import LLMConfigError

ENV_KEYS = [
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
    "LLM_STREAM_INCLUDE_USAGE",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def test_missing_fields_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = LLMConfig.from_env()
    assert cfg.missing_fields() == ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"]
    with pytest.raises(LLMConfigError) as exc_info:
        cfg.validate()
    assert "LLM_BASE_URL" in str(exc_info.value)
    assert exc_info.value.to_dict()["missing"] == ["LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"]


def test_from_env_parses_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1/")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "m1")
    monkeypatch.setenv("LLM_STREAM", "false")
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.3")

    cfg = LLMConfig.from_env()
    assert cfg.base_url == "https://api.example.com/v1"  # 去掉尾部斜杠
    assert cfg.endpoint_url == "https://api.example.com/v1/chat/completions"
    assert cfg.stream is False
    assert cfg.max_attempts == 2
    assert cfg.temperature == 0.3
    cfg.validate()  # 不抛异常


def test_redacted_hides_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-super-secret")
    monkeypatch.setenv("LLM_MODEL", "m1")
    cfg = LLMConfig.from_env()
    snapshot = cfg.redacted()
    assert "sk-super-secret" not in str(snapshot)
    assert snapshot["api_key_set"] is True


@pytest.mark.parametrize(
    "key,value",
    [
        ("LLM_STREAM", "maybe"),
        ("LLM_MAX_ATTEMPTS", "0"),
        ("LLM_TEMPERATURE", "abc"),
    ],
)
def test_invalid_env_values(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    monkeypatch.setenv(key, value)
    with pytest.raises(LLMConfigError):
        LLMConfig.from_env()


def test_timeout_ordering_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_S", "60")
    monkeypatch.setenv("LLM_TOTAL_TIMEOUT_S", "30")
    cfg = LLMConfig.from_env()
    with pytest.raises(LLMConfigError):
        cfg.validate()


def test_thinking_default_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    cfg = LLMConfig.from_env()
    assert cfg.thinking == "default"
    cfg.validate()


def test_thinking_invalid_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_THINKING", "sometimes")
    cfg = LLMConfig.from_env()
    with pytest.raises(LLMConfigError) as exc_info:
        cfg.validate()
    assert "LLM_THINKING" in str(exc_info.value)


def test_load_env_file_rejects_gbk(tmp_path) -> None:
    """GBK 编码的 .env 必须给出可读错误，而不是 UnicodeDecodeError。"""
    from server.config import load_env_file

    bad = tmp_path / ".env"
    bad.write_bytes("LLM_MODEL=测试\n".encode("gbk"))
    with pytest.raises(LLMConfigError) as exc_info:
        load_env_file(bad)
    assert "UTF-8" in str(exc_info.value)


def test_load_env_file_missing_is_noop(tmp_path) -> None:
    from server.config import load_env_file

    load_env_file(tmp_path / "not-there.env")  # 不抛异常
