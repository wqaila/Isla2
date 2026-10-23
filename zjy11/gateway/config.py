"""仅从本项目配置和显式环境变量加载设置，不复用其他产品凭据。"""
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    device_token: str
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    cloud_enabled: bool = False
    temperature: float = 0.7
    max_tokens: int = 256
    timeout: float = 45.0
    rate_limit: int = 20
    trust_env: bool = False
    host: str = "0.0.0.0"
    port: int = 8111
    admin_token: str = ""

    def __post_init__(self):
        if len(self.device_token) < 24 or not self.device_token.isascii() or any(c.isspace() for c in self.device_token):
            raise ValueError("设备令牌必须至少 24 个 ASCII 字符，不能包含空白。")
        if self.base_url:
            url = urlsplit(self.base_url)
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("模型根地址必须是无凭据、无查询参数的 HTTPS URL。")
        if not 1 <= self.port <= 65535 or not 1 <= self.rate_limit <= 120:
            raise ValueError("端口或每分钟请求上限无效。")
        if not 1 <= self.max_tokens <= 1024 or not 1 <= self.timeout <= 120 or not 0 <= self.temperature <= 2:
            raise ValueError("模型输出上限、超时或温度超出允许范围。")

    @property
    def configured(self):
        return bool(self.api_key.strip() and self.base_url.strip() and self.model.strip())


def load_settings(root: Path = ROOT) -> Settings:
    values = {**dotenv_values(root / ".env"), **os.environ}

    def value(name, default=""):
        return str(values.get(name) or default).strip()

    def enabled(name):
        raw = value(name, "false").lower()
        if raw not in {"true", "false", "1", "0"}:
            raise ValueError(f"{name} 必须是 true 或 false。")
        return raw in {"true", "1"}

    token = value("GATEWAY_DEVICE_TOKEN")
    if not token:
        directory = root / ".local"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "device_token"
        if not path.exists():
            try:
                with path.open("x", encoding="utf-8") as handle:
                    handle.write(secrets.token_urlsafe(32) + "\n")
            except FileExistsError:
                pass
        token = path.read_text(encoding="utf-8").strip()
    return Settings(
        device_token=token,
        api_key=value("LLM_API_KEY"),
        base_url=value("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
        model=value("LLM_MODEL", "deepseek-chat"),
        cloud_enabled=enabled("GATEWAY_CLOUD_ENABLED"),
        temperature=float(value("LLM_TEMPERATURE", "0.7")),
        max_tokens=int(value("LLM_MAX_TOKENS", "256")),
        timeout=float(value("LLM_TIMEOUT_SECONDS", "45")),
        rate_limit=int(value("GATEWAY_RATE_LIMIT", "20")),
        trust_env=enabled("LLM_TRUST_ENV"),
        host=value("GATEWAY_HOST", "0.0.0.0"),
        port=int(value("GATEWAY_PORT", "8111")),
        admin_token=_admin_token(root),
    )


def _admin_token(root: Path) -> str:
    """设置页管理令牌：与本机设备令牌分开生成、分开保存。

    之所以不塞进 .env：它只用于本机网页登录，属于运维凭据，
    放在 .local/ 下（已被 .gitignore 排除）不容易被误提交。
    """
    directory = root / ".local"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "admin_token"
    if not path.exists():
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(secrets.token_urlsafe(32) + "\n")
        except FileExistsError:
            pass
    return path.read_text(encoding="utf-8").strip()
