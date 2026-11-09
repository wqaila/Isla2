"""配置系统测试。

重点验证原提示词的要求：
"不能留下配置看似生效但实际未使用的参数"。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from custom_voice.config.loader import load_config, validate_consumed
from custom_voice.config.schema import (
    CONSUMED_FIELDS,
    EngineKind,
    GPUConfig,
    Precision,
    find_stale_registrations,
    find_unconsumed_fields,
)
from custom_voice.core.errors import ConfigError


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# 正常路径
# ----------------------------------------------------------------------


def test_default_config_loads(tmp_path: Path) -> None:
    """仓库自带的 configs/gpu.yaml 必须能加载通过。"""
    cfg = load_config()
    assert cfg.engine is EngineKind.STUB
    assert cfg.port == 50001
    assert cfg.is_loopback()


def test_default_precision_is_fp32() -> None:
    """实测 fp16 更慢更耗显存，默认必须是 fp32。"""
    cfg = load_config()
    assert cfg.precision is Precision.FP32


def test_config_is_frozen() -> None:
    """配置不可变，防止运行期被意外改写。"""
    cfg = load_config()
    with pytest.raises(Exception):
        cfg.port = 1234  # type: ignore[misc]


# ----------------------------------------------------------------------
# 非法值
# ----------------------------------------------------------------------


def test_unknown_field_rejected(tmp_path: Path) -> None:
    """拼写错误的字段必须报错，不能被静默忽略。"""
    p = _write_yaml(tmp_path / "bad.yaml", {"engine": "stub", "prot": 50001})
    with pytest.raises(ConfigError) as ei:
        load_config(p)
    assert "prot" in str(ei.value)


def test_out_of_range_port(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path / "bad.yaml", {"port": 80})
    with pytest.raises(ConfigError) as ei:
        load_config(p)
    assert "port" in str(ei.value)


def test_zero_concurrency_rejected(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path / "bad.yaml", {"max_concurrent_requests": 0})
    with pytest.raises(ConfigError) as ei:
        load_config(p)
    assert "max_concurrent_requests" in str(ei.value)


def test_invalid_enum_rejected(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path / "bad.yaml", {"precision": "int8"})
    with pytest.raises(ConfigError) as ei:
        load_config(p)
    assert "precision" in str(ei.value)


def test_missing_file() -> None:
    with pytest.raises(ConfigError) as ei:
        load_config(Path("/nonexistent/nope.yaml"))
    assert "不存在" in str(ei.value)


def test_malformed_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("engine: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


# ----------------------------------------------------------------------
# D10 安全约束：对外开放监听必须带 token
# ----------------------------------------------------------------------


def test_non_loopback_without_token_rejected(tmp_path: Path) -> None:
    """host=0.0.0.0 且无 token 时必须拒绝启动。"""
    p = _write_yaml(tmp_path / "bad.yaml", {"host": "0.0.0.0", "auth_token": ""})
    with pytest.raises(ConfigError) as ei:
        load_config(p)
    assert "auth_token" in str(ei.value)


def test_non_loopback_with_token_accepted(tmp_path: Path) -> None:
    p = _write_yaml(tmp_path / "ok.yaml", {"host": "0.0.0.0", "auth_token": "secret"})
    cfg = load_config(p)
    assert cfg.host == "0.0.0.0"
    assert not cfg.is_loopback()


def test_loopback_without_token_accepted() -> None:
    cfg = load_config(overrides={"host": "127.0.0.1", "auth_token": ""})
    assert cfg.is_loopback()


# ----------------------------------------------------------------------
# 消费登记机制（核心）
# ----------------------------------------------------------------------


def test_all_fields_are_consumed() -> None:
    """默认配置的所有字段都必须登记消费方。"""
    cfg = load_config()
    assert find_unconsumed_fields(cfg) == []


def test_no_stale_registrations() -> None:
    """登记表里不能有配置中已不存在的字段。"""
    cfg = load_config()
    assert find_stale_registrations(cfg) == []


def test_unconsumed_field_detected() -> None:
    """人为构造未登记字段，必须被检出。"""
    cfg = load_config()
    fields = set(cfg.model_fields)
    # 找一个确实在登记表里的字段，模拟"忘记登记"
    victim = "speaker_cache_max_items"
    assert victim in fields

    import custom_voice.config.schema as schema_mod

    original = dict(schema_mod.CONSUMED_FIELDS)
    try:
        del schema_mod.CONSUMED_FIELDS[victim]
        with pytest.raises(ConfigError) as ei:
            validate_consumed(cfg)
        assert victim in str(ei.value)
    finally:
        schema_mod.CONSUMED_FIELDS.clear()
        schema_mod.CONSUMED_FIELDS.update(original)


def test_stale_registration_detected() -> None:
    cfg = load_config()
    import custom_voice.config.schema as schema_mod

    original = dict(schema_mod.CONSUMED_FIELDS)
    try:
        schema_mod.CONSUMED_FIELDS["ghost_field"] = "nowhere"
        with pytest.raises(ConfigError) as ei:
            validate_consumed(cfg)
        assert "ghost_field" in str(ei.value)
    finally:
        schema_mod.CONSUMED_FIELDS.clear()
        schema_mod.CONSUMED_FIELDS.update(original)


def test_consumed_fields_cover_all_model_fields() -> None:
    """静态检查：登记表与模型字段必须完全一致。"""
    assert set(CONSUMED_FIELDS) == set(GPUConfig.model_fields)


# ----------------------------------------------------------------------
# 环境变量覆盖
# ----------------------------------------------------------------------


def test_env_override_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZJY14_PORT", "50999")
    cfg = load_config()
    assert cfg.port == 50999


def test_env_override_bool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZJY14_ENABLE_COMPILE", "true")
    cfg = load_config()
    assert cfg.enable_compile is True
