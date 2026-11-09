"""配置加载：YAML + 环境变量覆盖 + 消费登记校验。

加载顺序（后者覆盖前者）：
1. 代码内默认值（``GPUConfig`` 的 Field default）
2. ``configs/gpu.yaml``
3. 环境变量 ``ZJY14_<大写字段名>``
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..core.errors import ConfigError
from .schema import (
    CONSUMED_FIELDS,
    GPUConfig,
    find_stale_registrations,
    find_unconsumed_fields,
)

#: 环境变量前缀
ENV_PREFIX = "ZJY14_"

#: 项目根目录（custom_voice/config/loader.py -> 上两级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

#: 默认配置文件
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "gpu.yaml"


def _coerce_env_value(raw: str) -> Any:
    """把环境变量字符串转成合适的 Python 类型。

    YAML 的 safe_load 能正确处理 bool/int/float，复用它避免自己写解析。
    """
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"配置文件 {path} 不是合法 UTF-8（字节位置 {exc.start}）。"
            "请用 VS Code 另存为 UTF-8。"
        ) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 {path} 不是合法 YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件 {path} 顶层必须是映射（键值对），实际是 {type(data).__name__}")
    return data


def _env_overrides() -> dict[str, Any]:
    """收集 ZJY14_* 环境变量。

    只接受 ``GPUConfig`` 中确实存在的字段名，忽略其他（如 ``ZJY14_DATA_DIR``，
    它属于路径配置而非引擎配置）。否则会被 ``extra='forbid'`` 判为非法字段。
    """
    known = set(GPUConfig.model_fields)
    overrides: dict[str, Any] = {}
    for key, raw in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        field = key[len(ENV_PREFIX):].lower()
        if field not in known:
            continue
        if not raw.strip():
            continue
        overrides[field] = _coerce_env_value(raw.strip())
    return overrides


def validate_consumed(cfg: GPUConfig) -> None:
    """校验每个配置字段都有消费方。

    这是原提示词"不能留下配置看似生效但实际未使用的参数"的落地机制。
    """
    unconsumed = find_unconsumed_fields(cfg)
    if unconsumed:
        raise ConfigError(
            "以下配置字段没有登记消费方，等于配了也不生效："
            f"{', '.join(unconsumed)}。"
            "请在 custom_voice/config/schema.py 的 CONSUMED_FIELDS 中登记，"
            "或从配置中删除该字段。"
        )
    stale = find_stale_registrations(cfg)
    if stale:
        raise ConfigError(
            f"CONSUMED_FIELDS 中登记了配置里不存在的字段：{', '.join(stale)}。"
            "请删除这些过期登记，避免登记表腐化。"
        )


def load_config(
    path: Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    check_consumed: bool = True,
) -> GPUConfig:
    """加载并校验配置。

    :param path: 配置文件路径，默认 ``configs/gpu.yaml``
    :param overrides: 额外覆盖（优先级最高，主要用于测试）
    :param check_consumed: 是否校验字段消费登记
    :raises ConfigError: 文件缺失、YAML 非法、字段未知、值越界、字段未消费
    """
    cfg_path = path or DEFAULT_CONFIG_PATH
    raw = _load_yaml(cfg_path)
    raw.update(_env_overrides())
    if overrides:
        raw.update(overrides)

    try:
        cfg = GPUConfig(**raw)
    except ValidationError as exc:
        # 把 Pydantic 的错误转成可读中文，逐条列出字段
        lines: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "(根)"
            lines.append(f"  - {loc}: {err['msg']}")
        raise ConfigError(
            f"配置文件 {cfg_path} 校验失败：\n" + "\n".join(lines)
        ) from exc

    if check_consumed:
        validate_consumed(cfg)
    return cfg


def describe_config_sources(path: Path | None = None) -> dict[str, Any]:
    """返回配置来源摘要，供 /api/v1/system/status 展示。"""
    cfg_path = path or DEFAULT_CONFIG_PATH
    env_keys = sorted(
        k for k in os.environ if k.startswith(ENV_PREFIX)
    )
    return {
        "config_path": str(cfg_path),
        "config_exists": cfg_path.is_file(),
        "env_overrides": env_keys,
        "consumed_field_count": len(CONSUMED_FIELDS),
    }


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ENV_PREFIX",
    "PROJECT_ROOT",
    "describe_config_sources",
    "load_config",
    "validate_consumed",
]
