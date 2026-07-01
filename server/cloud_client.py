"""
云端 API 客户端
支持所有 OpenAI 兼容的云端 API（OpenAI、DeepSeek、通义千问、智谱 GLM 等）
自动注入爱莉希雅人设 System Prompt
"""
import json
import time
import httpx
from elysia_prompt import build_messages

# ===== 支持的云端 API 提供商 =====
CLOUD_PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "description": "DeepSeek-V3，性价比极高",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "description": "GPT-4o，效果最好",
    },
    "qwen": {
        "name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "description": "阿里通义千问，国内访问快",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "description": "智谱 GLM-4，免费额度大",
    },
    "moonshot": {
        "name": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "description": "Kimi，长文本能力强",
    },
    "custom": {
        "name": "自定义",
        "base_url": "",
        "model": "",
        "description": "自定义 OpenAI 兼容 API",
    },
}


class CloudClient:
    """云端 API 客户端（OpenAI 兼容协议）"""

    def __init__(self):
        self.provider = "deepseek"  # 默认使用 DeepSeek
        self.api_key = ""
        self.base_url = CLOUD_PROVIDERS["deepseek"]["base_url"]
        self.model = CLOUD_PROVIDERS["deepseek"]["model"]
        self.temperature = 0.6
        self.max_tokens = 256
        self.use_fewshot = True
        self._client = httpx.AsyncClient(timeout=120.0)  # FIX #2: 复用连接池

    async def close(self):
        """关闭客户端连接池"""
        await self._client.aclose()

    async def _reset_client(self):
        """重置 httpx 客户端连接池（切换提供商时需要，避免旧连接缓存）"""
        await self._client.aclose()
        self._client = httpx.AsyncClient(timeout=120.0)

    def configure(self, provider: str = None, api_key: str = None,
                  base_url: str = None, model: str = None,
                  temperature: float = None, max_tokens: int = None,
                  use_fewshot: bool = None):
        """配置云端 API"""
        old_base_url = self.base_url
        if provider and provider in CLOUD_PROVIDERS:
            self.provider = provider
            info = CLOUD_PROVIDERS[provider]
            self.base_url = info["base_url"]
            self.model = info["model"]
        
        if api_key is not None:
            self.api_key = api_key
        if base_url is not None:
            self.base_url = base_url
        if model is not None:
            self.model = model
        if temperature is not None:
            self.temperature = temperature
        if max_tokens is not None:
            self.max_tokens = max_tokens
        if use_fewshot is not None:
            self.use_fewshot = use_fewshot
        
        # 如果 base_url 变了，异步重置连接池（避免旧连接缓存到新 host）
        if self.base_url != old_base_url:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self._reset_client())
                else:
                    loop.run_until_complete(self._reset_client())
            except RuntimeError:
                pass

    def get_config(self) -> dict:
        """获取当前配置"""
        return {
            "provider": self.provider,
            "provider_name": CLOUD_PROVIDERS.get(self.provider, {}).get("name", "未知"),
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "use_fewshot": self.use_fewshot,
            "api_key_set": bool(self.api_key),
        }

    def is_configured(self) -> bool:
        """是否已配置"""
        return bool(self.api_key and self.base_url and self.model)

    async def chat(self, user_message: str, history: list = None, memory_context: str = "", learning_context: str = "", emotion_context: str = "", time_context: str = ""):
        """
        发送聊天请求（流式）
        
        Args:
            user_message: 用户消息
            history: 历史消息 [{"role": "user/assistant", "content": "..."}]
            memory_context: RAG 记忆上下文
            learning_context: 用户学习引擎的个性化上下文
            emotion_context: 情感识别引擎的情绪自适应指导
            time_context: 时间上下文感知信息
        
        Yields:
            {"content": "...", "done": bool, "tokens_per_sec": float, ...}
        """
        if not self.is_configured():
            yield {
                "content": "云端 API 未配置，请在设置中填写 API Key~",
                "done": True,
                "tokens_per_sec": 0,
                "total_tokens": 0,
                "response_time_ms": 0,
            }
            return

        # 构建消息（自动注入爱莉希雅人设 + 时间上下文 + Few-Shot + 记忆 + 学习上下文 + 情感指导）
        messages = build_messages(user_message, history, few_shot=self.use_fewshot, memory_context=memory_context, learning_context=learning_context, emotion_context=emotion_context, time_context=time_context)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": 0.9,
            "frequency_penalty": 0.6,
            "presence_penalty": 0.3,
            "stream": True,
        }

        start_time = time.time()

        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                    if resp.status_code != 200:
                        error_body = ""
                        async for chunk in resp.aiter_bytes():
                            error_body += chunk.decode("utf-8", errors="replace")
                        yield {
                            "content": f"云端 API 错误 ({resp.status_code}): {error_body[:200]}",
                            "done": True,
                            "tokens_per_sec": 0,
                            "total_tokens": 0,
                            "response_time_ms": int((time.time() - start_time) * 1000),
                        }
                        return

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                yield {
                                    "content": "",
                                    "done": True,
                                    "tokens_per_sec": 0,
                                    "total_tokens": 0,
                                    "response_time_ms": int((time.time() - start_time) * 1000),
                                }
                                break
                            try:
                                data = json.loads(data_str)
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield {
                                        "content": content,
                                        "done": False,
                                        "tokens_per_sec": 0,
                                        "total_tokens": 0,
                                        "response_time_ms": 0,
                                    }
                            except json.JSONDecodeError:
                                continue

        except httpx.ConnectError as e:
            yield {
                "content": f"无法连接到云端 API，请检查网络连接~",
                "done": True,
                "tokens_per_sec": 0,
                "total_tokens": 0,
                "response_time_ms": int((time.time() - start_time) * 1000),
            }
        except httpx.TimeoutException:
            yield {
                "content": "云端 API 请求超时，请稍后再试~",
                "done": True,
                "tokens_per_sec": 0,
                "total_tokens": 0,
                "response_time_ms": int((time.time() - start_time) * 1000),
            }
        except Exception as e:
            yield {
                "content": f"云端 API 异常: {str(e)[:100]}",
                "done": True,
                "tokens_per_sec": 0,
                "total_tokens": 0,
                "response_time_ms": int((time.time() - start_time) * 1000),
            }

    async def test_connection(self) -> dict:
        """测试云端 API 连接"""
        if not self.is_configured():
            return {"status": "error", "message": "API Key 未配置"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是一个测试助手"},
                {"role": "user", "content": "回复OK"},
            ],
            "max_tokens": 10,
        }

        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return {
                    "status": "success",
                    "message": f"连接成功！模型回复: {content}",
                    "model": self.model,
                    "provider": CLOUD_PROVIDERS.get(self.provider, {}).get("name", self.provider),
                }
            else:
                return {
                    "status": "error",
                    "message": f"HTTP {resp.status_code}: {resp.text[:200]}",
                }
        except Exception as e:
            return {
                "status": "error",
                "message": f"连接失败: {str(e)}",
            }


# 全局实例
cloud_client = CloudClient()
