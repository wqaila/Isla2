"""
PC 端服务配置
"""
import os
from pathlib import Path

# ===== 服务配置 =====
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8080

# ===== Ollama 配置 =====
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "elysia-lora"  # 默认模型名

# ===== 数据库配置 =====
BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "elysia_server.db"

# ===== 日志配置 =====
LOG_DIR = BASE_DIR / "data" / "logs"
LOG_LEVEL = "INFO"


def ensure_directories():
    """确保数据和日志目录存在（由启动逻辑调用，避免模块导入时的副作用）"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

# ===== 连接配置 =====
MAX_CONNECTIONS = 10
HEARTBEAT_INTERVAL = 30  # 秒
REQUEST_TIMEOUT = 120    # 秒

# ===== API 认证配置 =====
# 留空则不需要认证（适合局域网使用）
# 设置后所有 API 请求需要在 Header 中携带: Authorization: Bearer <token>
API_TOKEN = ""

# ===== 速率限制配置 =====
# 每个 IP 每分钟最大请求数（0 = 不限制）
RATE_LIMIT_PER_MINUTE = 120
# 聊天接口每分钟最大请求数（单独限制，更严格）
CHAT_RATE_LIMIT_PER_MINUTE = 60

# ===== 记忆系统配置 =====
# 可选值: "tfidf" (轻量低内存), "chromadb" (精准语义搜索但占用内存大)
MEMORY_BACKEND = "tfidf"

# ===== 云端 API 配置 =====
# 默认使用 DeepSeek，可通过 API 动态切换
CLOUD_PROVIDER = "deepseek"
CLOUD_API_KEY = ""  # 在此填入你的 API Key，或通过 API 动态设置
CLOUD_MODEL = ""    # 留空则使用提供商默认模型
CLOUD_TEMPERATURE = 0.7
CLOUD_MAX_TOKENS = 512
CLOUD_USE_FEWSHOT = True

# 模型来源：本地 Ollama 模型优先，云端 API 作为备选
# 可选值: "ollama", "cloud", "auto"
# auto 模式：优先使用 Ollama，如果 Ollama 不可用则自动切换到云端
MODEL_SOURCE = "auto"

# ===== 静态文件 =====
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
