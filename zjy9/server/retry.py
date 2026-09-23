"""异步重试工具：给网络调用加指数退避。

为什么需要：
    cloud_client 与 ollama_client 之前**完全没有重试**。后果是——
    - 云端：一次网络抖动就等于整轮对话失败；
    - 本地：Ollama 冷启动加载模型要 10~30 秒，这个窗口最容易连接失败/超时，
      而它恰恰是最需要重试的地方。

设计原则：
    只重试「连接层故障」与「服务端临时故障（5xx / 429）」。
    4xx 是请求本身的问题（比如 API Key 错、参数错），重试多少次都一样，
    只会白白拖长用户的等待时间，所以直接抛出。
"""
import asyncio

import httpx

# 值得重试的 httpx 异常：都属于「连接没建起来」或「连接中途断了」
_RETRYABLE_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
)


def is_retryable(exc: Exception) -> bool:
    """判断异常是否值得重试"""
    if isinstance(exc, _RETRYABLE_EXC):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 or code == 429
    return False


async def retry_async(fn, *args, attempts: int = 3, base_delay: float = 1.0,
                      max_delay: float = 8.0, on_retry=None, **kwargs):
    """带指数退避地执行 `await fn(*args, **kwargs)`。

    Args:
        attempts: 总尝试次数（含首次）。<=1 表示不重试。
        base_delay: 首次重试前等待秒数，之后按 2 倍递增。
        max_delay: 单次等待上限。
        on_retry: 可选回调 on_retry(第几次重试, 总次数, 等待秒数, 异常)，用于打日志。

    Returns:
        fn 的返回值。

    Raises:
        最后一次的异常（不可重试的异常会立即抛出，不等待）。
    """
    attempts = max(1, int(attempts))
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - 需要按类型判断后再决定是否重试
            last_exc = e
            if i == attempts - 1 or not is_retryable(e):
                raise
            delay = min(base_delay * (2 ** i), max_delay)
            if on_retry is not None:
                try:
                    on_retry(i + 1, attempts, delay, e)
                except Exception:
                    pass
            await asyncio.sleep(delay)
    # 理论上不可达：循环最后一轮必然 return 或 raise
    raise last_exc
