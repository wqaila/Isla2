"""
配置持久化模块
将运行时配置变更保存到 JSON 文件，服务重启后自动恢复
敏感字段（如 API Key）使用 Fernet 加密存储
"""
import json
import base64
import os
from pathlib import Path
from cryptography.fernet import Fernet
from config import BASE_DIR

_CONFIG_FILE = BASE_DIR / "data" / "runtime_config.json"
_KEY_FILE = BASE_DIR / "data" / ".encryption_key"

# 需要加密存储的敏感字段
_SENSITIVE_FIELDS = {"cloud_api_key", "api_token"}

# 默认配置（首次运行或文件不存在时使用）
_DEFAULT_CONFIG = {
    "cloud_provider": "deepseek",
    "cloud_api_key": "",
    "cloud_model": "",
    "cloud_temperature": 0.7,
    "cloud_max_tokens": 512,
    "cloud_use_fewshot": True,
    "model_source": "auto",
    "api_token": "",
    "rate_limit_per_minute": 30,
    "chat_rate_limit_per_minute": 15,
    "memory_backend": "tfidf",
    "memory_max_conversations": 5000,
    "memory_max_facts": 2000,
    "memory_max_summaries": 500,
    "request_timeout": 120,
    "max_connections": 10,
    "heartbeat_interval": 30,
}

_cached_config: dict | None = None
_cipher: Fernet | None = None


def _get_cipher() -> Fernet:
    """获取或创建 Fernet 加密器（密钥持久化到文件）"""
    global _cipher
    if _cipher is not None:
        return _cipher

    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)

    if _KEY_FILE.exists():
        with open(_KEY_FILE, "rb") as f:
            key = f.read()
    else:
        key = Fernet.generate_key()
        # 仅在 Windows 上设置隐藏属性
        try:
            if os.name == "nt":
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(_KEY_FILE), 2)  # FILE_ATTRIBUTE_HIDDEN
        except Exception:
            pass
        with open(_KEY_FILE, "wb") as f:
            f.write(key)

    _cipher = Fernet(key)
    return _cipher


def _encrypt_sensitive(config: dict) -> dict:
    """加密配置中的敏感字段，返回可安全存储的副本"""
    cipher = _get_cipher()
    safe = config.copy()
    for field in _SENSITIVE_FIELDS:
        if field in safe and safe[field]:
            safe[field] = cipher.encrypt(safe[field].encode()).decode()
    return safe


def _decrypt_sensitive(config: dict) -> dict:
    """解密配置中的敏感字段，返回可使用的副本"""
    cipher = _get_cipher()
    usable = config.copy()
    for field in _SENSITIVE_FIELDS:
        if field in usable and usable[field]:
            try:
                usable[field] = cipher.decrypt(usable[field].encode()).decode()
            except Exception:
                # 兼容旧版明文数据：解密失败则保持原值（可能是未加密的旧数据）
                pass
    return usable


def load_config() -> dict:
    """加载持久化配置，文件不存在时返回默认值。敏感字段自动解密。"""
    global _cached_config
    if _cached_config is not None:
        return _cached_config.copy()
    
    if _CONFIG_FILE.exists():
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # 解密敏感字段
            saved = _decrypt_sensitive(saved)
            # 合并默认值（防止新增字段缺失）
            config = {**_DEFAULT_CONFIG, **saved}
            _cached_config = config
            return config.copy()
        except Exception:
            pass
    
    _cached_config = _DEFAULT_CONFIG.copy()
    return _cached_config.copy()


def save_config(updates: dict) -> dict:
    """
    更新并保存配置。敏感字段自动加密后存储。
    
    Args:
        updates: 要更新的键值对
    
    Returns:
        更新后的完整配置（解密后，可直接使用）
    """
    global _cached_config
    current = load_config()
    current.update(updates)
    
    # 加密敏感字段后再写入磁盘
    safe_config = _encrypt_sensitive(current)
    
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(safe_config, f, ensure_ascii=False, indent=2)
    
    _cached_config = current  # 缓存解密后的版本
    return current.copy()


def get_value(key: str, default=None):
    """获取单个配置值"""
    config = load_config()
    return config.get(key, default)


def set_value(key: str, value):
    """设置单个配置值"""
    return save_config({key: value})
