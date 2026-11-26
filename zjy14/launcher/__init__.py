"""zjy14 语音链路一键启动。

只依赖标准库：启动器必须在「服务都没起来」时可用，
不能反过来依赖 FastAPI / torch / node。
"""

from __future__ import annotations

from .orchestrator import (
    SERVICES,
    SPEC_BY_NAME,
    Orchestrator,
    ServiceSpec,
    inspect_llm_env,
    port_listening,
    probe_health,
)

__all__ = [
    "Orchestrator",
    "SERVICES",
    "SPEC_BY_NAME",
    "ServiceSpec",
    "inspect_llm_env",
    "port_listening",
    "probe_health",
]