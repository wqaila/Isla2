"""
Ollama API 客户端
封装与 Ollama 服务的通信，支持流式和非流式请求
"""
import json
import time
import re
from collections import Counter
import httpx
from config import OLLAMA_BASE_URL, OLLAMA_MODEL, REQUEST_TIMEOUT, runtime
from retry import retry_async


def _retry_attempts() -> int:
    """模型调用的重试次数（含首次），可运行时配置"""
    try:
        return max(1, int(runtime("model_retry_attempts", 3)))
    except Exception:
        return 3


def _on_retry_log(attempt: int, total: int, delay: float, exc: Exception) -> None:
    print(f"[Ollama] 第 {attempt}/{total - 1} 次重试，{delay:.1f}s 后重来（{type(exc).__name__}）")


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
    # 上下文窗口。
    # 之前是 2048，但每次请求要装：人设 System Prompt + 8 组 Few-Shot + RAG 记忆
    # (≤800字) + 用户画像(≤600字) + 情绪/时间上下文 + 最多 20 条历史，
    # 2048 根本装不下，Ollama 会静默截断，而 System Prompt 排在消息列表最前面，
    # 往往最先被丢掉 —— 人设直接崩坏。4096 才能容纳上述内容。
    "num_ctx": 4096,
}

# 安全硬限制：最大输出字符数（作为最后防线）
# 注意：这是"防跑飞"的兜底阈值，故意设得比人设要求的 150 字宽松，
# 避免在句子中间生硬切断；正常情况下由 num_predict 控制长度。
MAX_OUTPUT_CHARS = 400

# 中文 1 token ≈ 1.5 个字符（保守估计）
_CHARS_PER_TOKEN = 1.5

# ===== 动态回复长度映射 =====
# 目标长度与人设 System Prompt 里声明的字数要求保持一致：
#   普通问候 15-30 字 / 日常聊天 30-60 字 / 复杂问题 最多 120 字
# 这里按"目标字数"反推 num_predict，保证模型被约束在同一个尺度上，
# 而不是"提示词说最多100字、采样参数却允许生成 350 token（约500字）"。
def _calc_dynamic_num_predict(user_msg_len: int) -> int:
    """
    根据用户消息长度动态计算 num_predict（最大生成 token 数）

    映射关系（目标字数 → token 数，含少量余量）：
        短问候   ~35 字  → 33 token
        短句     ~70 字  → 57 token
        普通对话 ~120 字 → 90 token
        长问题   ~150 字 → 110 token
        超长输入 ~200 字 → 143 token
    """
    if user_msg_len <= 5:
        target_chars = 35
    elif user_msg_len <= 15:
        target_chars = 70
    elif user_msg_len <= 50:
        target_chars = 120
    elif user_msg_len <= 150:
        target_chars = 150
    else:
        target_chars = 200
    return int(target_chars / _CHARS_PER_TOKEN) + 10

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

    async def check_status(self, attempts: int = 1) -> dict:
        """检查 Ollama 服务状态。

        attempts 默认 1 —— 因为它同时被健康探针（/health）调用，探针必须快速
        返回，不能在 Ollama 挂掉时还退避重试好几秒。需要"等 Ollama 起来"的
        场景（服务启动、自动路由）显式传更大的值。
        """
        try:
            resp = await retry_async(
                self._client.get, f"{self.base_url}/api/tags",
                attempts=attempts, on_retry=_on_retry_log,
            )
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
        """获取模型详细信息（带重试）"""
        try:
            resp = await retry_async(
                self._client.post, f"{self.base_url}/api/show",
                json={"name": self.model},
                attempts=_retry_attempts(), on_retry=_on_retry_log,
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
        # 允许通过运行时配置调整上下文窗口（显存/内存吃紧时可以调小）
        options["num_ctx"] = int(runtime("num_ctx", GENERATION_OPTIONS["num_ctx"]))

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
        # 已经通过 yield 发给客户端的字符数。
        # 流式协议只能追加、不能撤回，所以任何"截断"都必须以 sent_len 为基准
        # 只补发增量，否则会把已经发过的内容重复发一遍。
        sent_len = 0

        if stream:
            # 流式请求
            try:
                # 建连阶段可重试：Ollama 未启动/正在启动时会 ConnectError，
                # 多试几次往往就能连上；5xx（服务未就绪）也一并重试。
                # 注意：开始吐字之后（sent_len > 0）绝不能重试——流式协议只能追加，
                # 重试会把已经发给客户端的内容再发一遍。
                _req = self._client.build_request(
                    "POST", f"{self.base_url}/api/chat", json=payload)

                async def _open_stream():
                    r = await self._client.send(_req, stream=True)
                    if r.status_code >= 500:
                        # 先把失败的响应关掉再抛，避免连接泄漏
                        await r.aclose()
                        raise httpx.HTTPStatusError(
                            f"HTTP {r.status_code}", request=_req, response=r)
                    return r

                resp = await retry_async(
                    _open_stream,
                    attempts=_retry_attempts(), on_retry=_on_retry_log,
                )
                try:
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
                                # 只补发"客户端还没收到、但截断后仍应保留"的那一段
                                if len(truncated) > sent_len:
                                    remaining = truncated[sent_len:]
                                    yield {
                                        "content": remaining,
                                        "done": False,
                                        "tokens_per_sec": 0,
                                        "total_tokens": 0,
                                        "response_time_ms": 0,
                                    }
                                    sent_len = len(truncated)
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
                                        if len(truncated) > sent_len:
                                            remaining = truncated[sent_len:]
                                            yield {
                                                "content": remaining,
                                                "done": False,
                                                "tokens_per_sec": 0,
                                                "total_tokens": 0,
                                                "response_time_ms": 0,
                                            }
                                            sent_len = len(truncated)
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
                                sent_len += len(content)

                            # 最终重复检测。
                            # 注意：走到这里内容已经流式发给了客户端，协议上无法"撤回"，
                            # 所以这里只记录告警并正常收尾。
                            # （原实现会把整段 truncated 当成新内容再 yield 一次，
                            #   导致客户端看到回复重复一整遍。）
                            if not repetition_detected and _detect_repetition(full_content):
                                truncated = _truncate_at_repetition(full_content)
                                print(
                                    f"[Ollama] 检测到重复输出：已生成 {len(full_content)} 字符，"
                                    f"理想截断点 {len(truncated)} 字符"
                                    f"（内容已流式发送，无法回撤，仅记录告警）"
                                )

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
                        sent_len += len(content)
                finally:
                    await resp.aclose()

            except httpx.ReadTimeout:
                yield _make_error_response("[错误] Ollama 响应超时，模型可能正在加载中", start_time)
            except httpx.ConnectError:
                yield _make_error_response("[错误] 无法连接到 Ollama 服务，请确认 Ollama 已启动", start_time)
            except Exception as e:
                yield _make_error_response(f"[错误] Ollama 请求异常: {str(e)[:200]}", start_time)
        else:
            # 非流式请求
            try:
                resp = await retry_async(
                    self._client.post, f"{self.base_url}/api/chat",
                    json=payload,
                    attempts=_retry_attempts(), on_retry=_on_retry_log,
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