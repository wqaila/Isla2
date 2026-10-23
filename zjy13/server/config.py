"""配置读取：全部来自环境变量 / .env，禁止硬编码密钥。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llm.base import LLMConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_TRUTHY = {"1", "true", "yes", "on", "y"}
_FALSY = {"0", "false", "no", "off", "n"}


def _env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None else value.strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    raise LLMConfigError(f"环境变量 {name} 不是合法布尔值: {raw!r}")


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise LLMConfigError(f"环境变量 {name} 不是合法数字: {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise LLMConfigError(f"环境变量 {name} 必须 >= {minimum}，当前为 {value}")
    return value


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise LLMConfigError(f"环境变量 {name} 不是合法整数: {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise LLMConfigError(f"环境变量 {name} 必须 >= {minimum}，当前为 {value}")
    return value


def load_env_file(path: Path | None = None) -> None:
    """加载 .env，并在编码损坏时给出可读的中文错误。

    Windows 上用 PowerShell 的 ``Add-Content`` 追加内容会按系统 GBK 编码写入，
    导致 ``.env`` 变成非法 UTF-8，dotenv 抛出的 UnicodeDecodeError 很难懂。
    这里提前检查并明确提示修复方式。
    """
    from dotenv import load_dotenv

    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.is_file():
        return
    try:
        env_path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LLMConfigError(
            f"配置文件 {env_path} 不是合法的 UTF-8 编码（字节位置 {exc.start}）。"
            "常见原因是用 PowerShell 的 Add-Content 或记事本按 GBK/ANSI 保存。"
            "请用 VS Code 或记事本「另存为」选择 UTF-8 重新保存。"
        ) from exc
    load_dotenv(env_path, override=False)


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """LLM 客户端配置。字段全部来自环境变量，可通过 ``from_env()`` 构造。"""

    provider: str = "openai_compat"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    chat_path: str = "/chat/completions"
    connect_timeout_s: float = 10.0
    request_timeout_s: float = 30.0
    total_timeout_s: float = 90.0
    max_attempts: int = 3
    backoff_base_s: float = 0.5
    backoff_max_s: float = 8.0
    temperature: float = 0.7
    max_tokens: int = 512
    stream: bool = True
    stream_include_usage: bool = False
    # 推理模型的思考开关。DeepSeek V4 系列默认开启思考，
    # 会让语音交互的首字延迟增加约 1~2.5 秒（本机实测）。
    # 取值："default"（不发送该参数）/ "disabled" / "enabled"
    thinking: str = "default"

    # ---- 构造 ----

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            provider=_env_str("LLM_PROVIDER", "openai_compat") or "openai_compat",
            base_url=_env_str("LLM_BASE_URL").rstrip("/"),
            api_key=_env_str("LLM_API_KEY"),
            model=_env_str("LLM_MODEL"),
            chat_path=_env_str("LLM_CHAT_PATH", "/chat/completions") or "/chat/completions",
            connect_timeout_s=_env_float("LLM_CONNECT_TIMEOUT_S", 10.0, minimum=0.1),
            request_timeout_s=_env_float("LLM_REQUEST_TIMEOUT_S", 30.0, minimum=0.1),
            total_timeout_s=_env_float("LLM_TOTAL_TIMEOUT_S", 90.0, minimum=0.1),
            max_attempts=_env_int("LLM_MAX_ATTEMPTS", 3, minimum=1),
            backoff_base_s=_env_float("LLM_BACKOFF_BASE_S", 0.5, minimum=0.0),
            backoff_max_s=_env_float("LLM_BACKOFF_MAX_S", 8.0, minimum=0.0),
            temperature=_env_float("LLM_TEMPERATURE", 0.7),
            max_tokens=_env_int("LLM_MAX_TOKENS", 512, minimum=1),
            stream=_env_bool("LLM_STREAM", True),
            stream_include_usage=_env_bool("LLM_STREAM_INCLUDE_USAGE", False),
            thinking=_env_str("LLM_THINKING", "default") or "default",
        )

    # ---- 校验 ----

    def missing_fields(self) -> list[str]:
        """列出缺失的必填配置项（环境变量名），供启动时明确报告。"""
        missing: list[str] = []
        if not self.base_url:
            missing.append("LLM_BASE_URL")
        if not self.api_key:
            missing.append("LLM_API_KEY")
        if not self.model:
            missing.append("LLM_MODEL")
        return missing

    def validate(self) -> None:
        missing = self.missing_fields()
        if missing:
            raise LLMConfigError(
                "云端 LLM 配置不完整，缺少: " + ", ".join(missing)
                + "。请复制 .env.example 为 .env 并填写实际服务商信息。",
                missing=missing,
            )
        if not self.base_url.startswith(("http://", "https://")):
            raise LLMConfigError(f"LLM_BASE_URL 必须以 http:// 或 https:// 开头: {self.base_url!r}")
        if self.backoff_max_s < self.backoff_base_s:
            raise LLMConfigError("LLM_BACKOFF_MAX_S 不能小于 LLM_BACKOFF_BASE_S")
        if self.request_timeout_s > self.total_timeout_s:
            raise LLMConfigError("LLM_REQUEST_TIMEOUT_S 不能大于 LLM_TOTAL_TIMEOUT_S")
        if self.thinking not in ("default", "disabled", "enabled"):
            raise LLMConfigError(
                f"LLM_THINKING 只能是 default / disabled / enabled，当前为 {self.thinking!r}"
            )

    # ---- 安全 ----

    @property
    def endpoint_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/{self.chat_path.lstrip('/')}"

    def redacted(self) -> dict[str, Any]:
        """可安全写入日志的配置快照：密钥只保留是否已设置。"""
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "endpoint": self.endpoint_url if self.base_url else "",
            "model": self.model,
            "api_key_set": bool(self.api_key),
            "stream": self.stream,
            "max_attempts": self.max_attempts,
            "request_timeout_s": self.request_timeout_s,
            "total_timeout_s": self.total_timeout_s,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "thinking": self.thinking,
        }


__all__ = ["LLMConfig", "PROJECT_ROOT", "load_env_file"]
