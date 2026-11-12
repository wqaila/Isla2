"""代理自动回退的测试。

背景（2026-11-12）：
用户反馈「LLM 报 network_error」。排查发现 ``.env`` 里配了
``LLM_PROXY=http://127.0.0.1:7897``（Clash），但 Clash 没启动。
每次请求都 ``ConnectError``，重试 3 次全失败。

而实测此时 **DeepSeek 可直连**（返回 401 即表示可达）：
    直连       : HTTP 401  (0.42s)
    代理 7897  : 失败 ConnectError (2.23s)

也就是说：配了一个「没开就必然失败」的代理，而直连本来是好的。

修复：新增 ``LLM_PROXY_FALLBACK``（默认 true）。
代理连接失败时自动改直连重试，并在本进程内记住该结果，
后续轮次不再浪费一次尝试去试探代理。

本文件锁住这个行为的关键点。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLIENT_PY = PROJECT_ROOT / "server" / "llm" / "client.py"
CONFIG_PY = PROJECT_ROOT / "server" / "config.py"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# 配置项
# ----------------------------------------------------------------------


def test_config_has_proxy_fallback_field() -> None:
    src = _read(CONFIG_PY)
    assert "proxy_fallback" in src, "LLMConfig 缺少 proxy_fallback 字段"


def test_proxy_fallback_defaults_true() -> None:
    """默认必须开启，否则用户仍会被「没开 Clash」卡住。"""
    from server.config import LLMConfig

    cfg = LLMConfig()
    assert cfg.proxy_fallback is True


def test_proxy_fallback_read_from_env(monkeypatch) -> None:
    from server.config import LLMConfig

    monkeypatch.setenv("LLM_PROXY_FALLBACK", "false")
    assert LLMConfig.from_env().proxy_fallback is False

    monkeypatch.setenv("LLM_PROXY_FALLBACK", "true")
    assert LLMConfig.from_env().proxy_fallback is True


def test_env_example_documents_fallback() -> None:
    text = _read(ENV_EXAMPLE)
    assert "LLM_PROXY_FALLBACK" in text, (
        ".env.example 没有说明回退开关，用户不知道有这个能力"
    )


# ----------------------------------------------------------------------
# 客户端实现
# ----------------------------------------------------------------------


def test_client_has_switch_to_direct() -> None:
    src = _read(CLIENT_PY)
    assert "_switch_to_direct" in src, "缺少切换到直连的方法"


def test_client_tracks_proxy_state() -> None:
    src = _read(CLIENT_PY)
    assert "_proxy_broken" in src, "缺少代理可用性标记"


def test_fallback_only_on_connect_error() -> None:
    """只在「连接失败」时回退。

    鉴权失败、限流等错误换个线路也一样，回退只是白白多打一次请求。
    """
    src = _read(CLIENT_PY)
    idx = src.index("_switch_to_direct()")
    # 往前找触发条件
    body = src[max(0, idx - 900):idx]
    assert "network_error" in body, "回退条件没有限定 network_error"
    assert "ConnectError" in body, "回退条件没有限定 ConnectError"


def test_fallback_respects_config_switch() -> None:
    """LLM_PROXY_FALLBACK=false 时必须不回退。"""
    src = _read(CLIENT_PY)
    idx = src.index("_switch_to_direct()")
    body = src[max(0, idx - 900):idx]
    assert "proxy_fallback" in body, "回退没有检查配置开关"


def test_fallback_does_not_consume_retry_budget() -> None:
    """回退不占用 max_attempts 配额，否则 3 次尝试里可能没机会重试。"""
    src = _read(CLIENT_PY)
    assert "retries_done -= 1" in src, (
        "回退时没有归还重试配额，max_attempts 较小时可能来不及重试"
    )


def test_client_rebuilds_without_proxy() -> None:
    """切换直连后必须重建 client（httpx 的 proxy 是构造时固定的）。"""
    src = _read(CLIENT_PY)
    idx = src.index("async def _switch_to_direct")
    body = src[idx:idx + 1400]
    assert "aclose" in body, "切换时没有关闭旧 client"
    assert "self._client = None" in body, "切换后没有置空 client 以触发重建"


def test_ensure_client_skips_proxy_when_broken() -> None:
    src = _read(CLIENT_PY)
    idx = src.index("def _ensure_client")
    body = src[idx:idx + 900]
    assert "_proxy_broken" in body, (
        "_ensure_client 没有检查 _proxy_broken，重建后仍会走代理"
    )


# ----------------------------------------------------------------------
# 行为验证（不依赖网络）
# ----------------------------------------------------------------------


def test_switch_to_direct_sets_flag() -> None:
    import asyncio

    from server.config import LLMConfig
    from server.llm import LLMClient

    cfg = LLMConfig(base_url="https://example.com", api_key="k", model="m",
                    proxy="http://127.0.0.1:7897")

    async def scenario() -> None:
        client = LLMClient(cfg)
        assert client._proxy_broken is None
        await client._switch_to_direct()
        assert client._proxy_broken is True
        # 再调一次不应报错（幂等）
        await client._switch_to_direct()

    asyncio.run(scenario())


def test_switch_to_direct_disabled_when_no_proxy() -> None:
    """没配代理时切换也应安全（幂等，不抛异常）。"""
    import asyncio

    from server.config import LLMConfig
    from server.llm import LLMClient

    cfg = LLMConfig(base_url="https://example.com", api_key="k", model="m", proxy="")

    async def scenario() -> None:
        client = LLMClient(cfg)
        await client._switch_to_direct()
        assert client._proxy_broken is True

    asyncio.run(scenario())
