"""
Ollama API 客户端
封装与 Ollama 服务的通信，支持流式和非流式请求
"""
import json
import time
import re
from collections import Counter
import httpx
from config import OLLAMA_BASE_URL, OLLAMA_MODEL, REQUEST_TIMEOUT


# ===== 生成参数配置（平衡创意与防重复） =====
# v2: 提升 temperature / mirostat_tau 以增加回复多样性，降低 repeat_penalty 让表达更自然
GENERATION_OPTIONS = {
    "temperature": 0.7,          # 0.5→0.7 增加创意性，让回复更多样
    "top_p": 0.9,                # 0.85→0.9 略微扩大词汇选择范围
    "top_k": 30,                 # 20→30 扩大候选词范围
    "repeat_penalty": 1.3,       # 1.5→1.3 降低惩罚强度，让语气词自然出现
    "repeat_last_n": 256,        # 128→256 扩大去重窗口，覆盖更多上下文
    "num_predict": 120,
    "mirostat": 2,
    "mirostat_tau": 4.5,         # 3.0→4.5 提升输出多样性
    "mirostat_eta": 0.1,
    "num_ctx": 2048,
}

# 安全硬限制：最大输出字符数（作为最后防线）
# FIX #11: 从 400 提升到 800，与动态 num_predict 最大 350 tokens (约 500-700 字符) 匹配
MAX_OUTPUT_CHARS = 800

# ===== 动态回复长度映射 =====
def _calc_dynamic_num_predict(user_msg_len: int) -> int:
    """
    根据用户消息长度动态计算 num_predict（最大生成 token 数）
    中文 1 token ≈ 1.5-2 个字符
    """
    if user_msg_len <= 5:
        return 30       # "你好" → 约20字简短回复
    elif user_msg_len <= 15:
        return 60       # 短句 → 约40字
    elif user_msg_len <= 50:
        return 120      # 普通对话 → 约80字
    elif user_msg_len <= 150:
        return 200      # 长问题 → 约140字
    else:
        return 350      # 超长输入 → 约250字

STOP_SEQUENCES = [
    "<|eot_id|>",
    "<|end_of_text|>",
    "</s>",
    "\nuser",
    "\nUser",
]


def _make_error_response(content: str, start_time: float, total_tokens: int = 0) -> dict:
    """统一的错误/终止响应构造"""
    return {
        "content": content,
        "done": True,
        "tokens_per_sec": 0,
        "total_tokens": total_tokens,
        "response_time_ms": int((time.time() - start_time) * 1000),
    }


def _detect_repetition(text: str) -> bool:
    """
    检测文本中是否存在重复模式（多层检测，从短到长）
    返回 True 表示检测到严重重复
    """
    if len(text) < 15:
        return False

    # 检测1: 连续相同句子重复（精确匹配，2次即触发）
    sentences = re.split(r'[。！？\n]', text)
    sentences = [s.strip() for s in sentences if s.strip() and len(s) > 3]
    if len(sentences) >= 2:
        for i in range(len(sentences) - 1):
            if sentences[i] == sentences[i+1]:
                return True

    # FIX #10: 短语级重复检测 - 短短语(2-3字)需要更多次重复才触发，避免误判正常语气词
    for phrase_len in range(2, 9):
        threshold = 5 if phrase_len <= 3 else 4 if phrase_len <= 5 else 3
        for i in range(0, len(text) - phrase_len * threshold, phrase_len):
            phrase = text[i:i+phrase_len]
            if re.match(r'^[^\w\u4e00-\u9fff]+$', phrase):
                continue
            count = 0
            pos = i
            while pos + phrase_len <= len(text) and text[pos:pos+phrase_len] == phrase:
                count += 1
                pos += phrase_len
            if count >= threshold:
                return True

    # 检测3: 中等长度重复（10-40字符的块重复2次即触发）
    for chunk_len in range(10, 41, 5):
        if len(text) < chunk_len * 3:
            continue
        end_chunk = text[-chunk_len:]
        if re.match(r'^[^\w\u4e00-\u9fff]+$', end_chunk):
            continue
        search_area = text[:-chunk_len]
        if end_chunk in search_area:
            return True

    # 检测4: 大段落级重复（50-200字符的块重复）
    for chunk_len in range(50, min(200, len(text) // 2), 10):
        end_chunk = text[-chunk_len:]
        search_area = text[:len(text) - chunk_len]
        if end_chunk in search_area:
            return True

    # FIX #10: 字符级频率异常 - 提高阈值避免正常中文常见字(的、了、是等)误触发
    if len(text) > 50:
        cn_chars = [c for c in text if '\u4e00' <= c <= '\u9fff']
        if cn_chars and len(cn_chars) > 20:
            counter = Counter(cn_chars)
            most_common_char, most_count = counter.most_common(1)[0]
            if most_count / len(cn_chars) > 0.20 and most_count > 15:
                return True

    return False


def _truncate_at_repetition(text: str) -> str:
    """
    在重复开始处截断文本，返回截断后的文本
    """
    if len(text) < 15:
        return text

    # 方法1: 从后向前找重复的块，截断到重复开始前
    for chunk_len in range(5, min(200, len(text) // 2), 3):
        if len(text) < chunk_len * 2:
            continue
        end_chunk = text[-chunk_len:]
        if re.match(r'^[^\w\u4e00-\u9fff]+$', end_chunk):
            continue
        search_area = text[:len(text) - chunk_len]
        pos = search_area.rfind(end_chunk)
        if pos > len(text) * 0.2:
            for j in range(min(pos + chunk_len, len(text) - 1), max(pos - 50, 0), -1):
                if j < len(text) and text[j] in '。！？\n':
                    result = text[:j+1].strip()
                    if len(result) > 10:
                        return result
            result = text[:pos].strip()
            if len(result) > 10:
                return result

    # 方法2: 找最后一个完整句子
    for i in range(len(text) - 1, max(len(text) // 3, 0), -1):
        if text[i] in '。！？\n':
            result = text[:i+1].strip()
            if len(result) > 10:
                return result

    return text


class OllamaClient:
    """Ollama API 客户端"""

    def __init__(self, base_url: str = OLLAMA_BASE_URL, model: str = OLLAMA_MODEL):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=float(REQUEST_TIMEOUT),
                write=30.0,
                pool=10.0,
            )
        )

    async def close(self):
        """关闭客户端连接池"""
        await self._client.aclose()

    async def check_status(self) -> dict:
        """检查 Ollama 服务状态"""
        try:
            resp = await self._client.get(f"{self.base_url}/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                models = [m["name"] for m in data.get("models", [])]
                model_available = any(
                    m == self.model or m.startswith(self.model + ":")
                    for m in models
                )
                return {
                    "status": "running",
                    "models": models,
                    "current_model": self.model,
                    "model_available": model_available,
                }
            return {"status": "error", "code": resp.status_code}
        except Exception as e:
            return {"status": "offline", "error": str(e)}

    async def get_model_info(self) -> dict:
        """获取模型详细信息"""
        try:
            resp = await self._client.post(
                f"{self.base_url}/api/show",
                json={"name": self.model},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    async def chat(self, messages: list, stream: bool = False):
        """
        发送聊天请求

        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            stream: 是否流式返回

        Yields (流式) 或 Returns (非流式):
            {"content": "...", "done": bool, "tokens_per_sec": float}
        """
        # 动态计算 num_predict：根据最后一条用户消息长度
        user_msg_len = 0
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_msg_len = len(msg.get("content", ""))
                break
        dynamic_num_predict = _calc_dynamic_num_predict(user_msg_len)

        options = GENERATION_OPTIONS.copy()
        options["num_predict"] = dynamic_num_predict

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": options,
            "keep_alive": "5m",
            "stop": STOP_SEQUENCES,
        }

        start_time = time.time()
        full_content = ""
        total_tokens = 0
        repetition_detected = False

        if stream:
            # 流式请求
            try:
                async with self._client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=payload,
                ) as resp:
                    if resp.status_code != 200:
                        error_body = ""
                        async for chunk in resp.aiter_text():
                            error_body += chunk
                        yield {
                            "content": f"[错误] Ollama API 返回 {resp.status_code}: {error_body[:200]}",
                            "done": True,
                            "tokens_per_sec": 0,
                            "total_tokens": 0,
                            "response_time_ms": 0,
                        }
                        return

                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # 提取内容
                        content = ""
                        done = False
                        if "message" in data:
                            content = data["message"].get("content", "")
                        done = data.get("done", False)

                        if content:
                            full_content += content

                            # 硬限制：超过最大字符数直接截断
                            if len(full_content) > MAX_OUTPUT_CHARS:
                                truncated = _truncate_at_repetition(full_content[:MAX_OUTPUT_CHARS])
                                if len(truncated) < len(full_content):
                                    remaining = truncated[len(full_content) - len(content):]
                                    if remaining:
                                        yield {
                                            "content": remaining,
                                            "done": False,
                                            "tokens_per_sec": 0,
                                            "total_tokens": 0,
                                            "response_time_ms": 0,
                                        }
                                response_time = int((time.time() - start_time) * 1000)
                                yield {
                                    "content": "",
                                    "done": True,
                                    "tokens_per_sec": 0,
                                    "total_tokens": total_tokens,
                                    "response_time_ms": response_time,
                                }
                                return

                            # 实时检测重复（每累积30个字符检测一次）
                            if len(full_content) > 30 and len(full_content) % 30 < 3:
                                if _detect_repetition(full_content):
                                    repetition_detected = True
                                    truncated = _truncate_at_repetition(full_content)
                                    if len(truncated) < len(full_content):
                                        remaining = truncated[len(full_content) - len(content):]
                                        if remaining:
                                            yield {
                                                "content": remaining,
                                                "done": False,
                                                "tokens_per_sec": 0,
                                                "total_tokens": 0,
                                                "response_time_ms": 0,
                                            }
                                        response_time = int((time.time() - start_time) * 1000)
                                        yield {
                                            "content": "",
                                            "done": True,
                                            "tokens_per_sec": 0,
                                            "total_tokens": total_tokens,
                                            "response_time_ms": response_time,
                                        }
                                        return

                        if done:
                            total_tokens = data.get("eval_count", 0)
                            response_time = int((time.time() - start_time) * 1000)

                            # FIX: yield 最后一个 chunk 的 content（如果有），避免丢失末尾 token
                            if content and not repetition_detected:
                                yield {
                                    "content": content,
                                    "done": False,
                                    "tokens_per_sec": 0,
                                    "total_tokens": 0,
                                    "response_time_ms": 0,
                                }

                            # 最终重复检测和截断
                            if not repetition_detected and _detect_repetition(full_content):
                                truncated = _truncate_at_repetition(full_content)
                                if len(truncated) < len(full_content):
                                    diff = truncated
                                    yield {
                                        "content": diff,
                                        "done": True,
                                        "tokens_per_sec": 0,
                                        "total_tokens": total_tokens,
                                        "response_time_ms": response_time,
                                    }
                                    return

                            tps = total_tokens / (response_time / 1000) if response_time > 0 else 0
                            yield {
                                "content": "",
                                "done": True,
                                "tokens_per_sec": round(tps, 1),
                                "total_tokens": total_tokens,
                                "response_time_ms": response_time,
                            }
                            return

                        # 非 done 的普通 chunk
                        yield {
                            "content": content,
                            "done": False,
                            "tokens_per_sec": 0,
                            "total_tokens": 0,
                            "response_time_ms": 0,
                        }

            except httpx.ReadTimeout:
                yield _make_error_response("[错误] Ollama 响应超时，模型可能正在加载中", start_time)
            except httpx.ConnectError:
                yield _make_error_response("[错误] 无法连接到 Ollama 服务，请确认 Ollama 已启动", start_time)
            except Exception as e:
                yield _make_error_response(f"[错误] Ollama 请求异常: {str(e)[:200]}", start_time)
        else:
            # 非流式请求
            try:
                resp = await self._client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                )
                response_time = int((time.time() - start_time) * 1000)

                if resp.status_code != 200:
                    yield {
                        "content": f"[错误] Ollama API 返回 {resp.status_code}: {resp.text[:200]}",
                        "done": True,
                        "tokens_per_sec": 0,
                        "total_tokens": 0,
                        "response_time_ms": response_time,
                    }
                    return

                data = resp.json()
                content = data.get("message", {}).get("content", "")
                total_tokens = data.get("eval_count", 0)

                # 重复检测和截断
                if _detect_repetition(content):
                    content = _truncate_at_repetition(content)

                tps = total_tokens / (response_time / 1000) if response_time > 0 else 0
                yield {
                    "content": content,
                    "done": True,
                    "tokens_per_sec": round(tps, 1),
                    "total_tokens": total_tokens,
                    "response_time_ms": response_time,
                }

            except httpx.ReadTimeout:
                yield _make_error_response("[错误] Ollama 响应超时", start_time)
            except httpx.ConnectError:
                yield _make_error_response("[错误] 无法连接到 Ollama 服务", start_time)
            except Exception as e:
                yield _make_error_response(f"[错误] {str(e)[:200]}", start_time)


# 全局单例
ollama_client = OllamaClient()