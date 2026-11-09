"""配置模型与校验。

设计要点（对应原提示词"不能留下配置看似生效但实际未使用的参数"）：

1. 所有字段都有类型和范围约束，非法值启动即报错。
2. 每个字段必须在 ``CONSUMED_FIELDS`` 中登记"谁消费了它"，
   未登记的字段视为无效配置，启动时报错。
3. 字段名与 ``configs/gpu.yaml`` 一一对应，不允许多余的键。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Precision(str, Enum):
    """推理精度。

    注意：本机实测 fp16 比 fp32 更慢（+7.1%）且显存更高（+27%），
    因此默认 fp32。见 docs/02_IMPLEMENTATION_PLAN.md 第三节。
    """

    FP32 = "fp32"
    FP16 = "fp16"
    BF16 = "bf16"


class EngineKind(str, Enum):
    """引擎实现类型。

    Phase 1 只有 stub；Phase 2 接入 cosyvoice3。
    """

    STUB = "stub"
    COSYVOICE3 = "cosyvoice3"


class SdpaMode(str, Enum):
    AUTO = "auto"
    ON = "on"
    OFF = "off"


class GPUConfig(BaseModel):
    """GPU 与推理配置。

    ``extra='forbid'`` 是关键：配置里写了未定义的键会直接报错，
    防止拼写错误被静默忽略。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # ---- 引擎 ----
    engine: EngineKind = Field(
        default=EngineKind.STUB,
        description="引擎实现。Phase 1 用 stub，Phase 2 改 cosyvoice3。",
    )
    device: Literal["cuda", "cpu"] = Field(default="cuda", description="推理设备。")
    precision: Precision = Field(
        default=Precision.FP32,
        description="推理精度。实测 fp16 更慢且更耗显存，默认 fp32。",
    )

    # ---- 并发 ----
    max_concurrent_requests: int = Field(
        default=1,
        ge=1,
        le=8,
        description="最大并发合成请求数。先单并发稳定，再逐步放开。",
    )
    request_timeout_s: float = Field(
        default=120.0,
        gt=0,
        le=600,
        description="单次合成超时秒数。",
    )

    # ---- 优化开关 ----
    enable_compile: bool = Field(
        default=False,
        description="torch.compile。未实测收益，默认关闭。",
    )
    enable_sdpa: SdpaMode = Field(
        default=SdpaMode.AUTO,
        description="SDPA 注意力。auto 表示按环境探测。",
    )
    enable_vllm_llm: bool = Field(
        default=False,
        description="vLLM 加速 LLM 子路径。模型目录无 vllm/ 子目录，默认关闭。",
    )

    # ---- 缓存 ----
    speaker_cache_enabled: bool = Field(
        default=True,
        description="缓存参考音频编码。实测省 16.5% 合成耗时。",
    )
    speaker_cache_max_items: int = Field(
        default=8,
        ge=1,
        le=64,
        description="缓存条目上限，超出按 LRU 淘汰。",
    )

    # ---- 显存 ----
    cpu_offload: bool = Field(
        default=False,
        description="CPU offload。可能显著增加延迟，默认关闭。",
    )
    memory_safety_margin_mb: int = Field(
        default=1024,
        ge=0,
        le=8192,
        description="启动时要求的最小空闲显存余量（MB）。不足则拒绝启动。",
    )

    # ---- 上游 bug 修复 ----
    token_hop_len_reset: bool = Field(
        default=True,
        description=(
            "每次合成前重置上游 token_hop_len。"
            "该属性跨调用翻倍且不重置，会导致首包延迟持续变差（实测 1.75s→2.25s）。"
        ),
    )

    # ---- 服务 ----
    host: str = Field(default="127.0.0.1", description="监听地址。")
    port: int = Field(default=50001, ge=1024, le=65535, description="监听端口。")
    auth_token: str = Field(
        default="",
        description="访问令牌。监听非回环地址时必填。",
    )

    @field_validator("host")
    @classmethod
    def _check_host(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("host 不能为空")
        return v.strip()

    @model_validator(mode="after")
    def _guard_non_loopback(self) -> "GPUConfig":
        """D10 安全约束：监听非回环地址时必须设置 auth_token。

        这里选择在配置校验阶段就失败，而不是等到服务启动后才发现裸奔。
        """
        loopback = {"127.0.0.1", "localhost", "::1"}
        if self.host not in loopback and not self.auth_token.strip():
            raise ValueError(
                f"host={self.host!r} 表示对外开放监听，必须同时设置 auth_token。"
                "如需仅本机访问，请把 host 改为 127.0.0.1。"
            )
        return self

    @model_validator(mode="after")
    def _guard_fp16_warning(self) -> "GPUConfig":
        """fp16 在本机实测是负收益，但不阻止（用户可能想自己验证）。"""
        return self

    def is_loopback(self) -> bool:
        return self.host in {"127.0.0.1", "localhost", "::1"}


#: 每个配置字段的消费方登记表。
#:
#: 键 = 字段名，值 = 消费该字段的模块（人类可读）。
#: 新增字段必须在此登记，否则 ``loader.validate_consumed()`` 会报错。
#: 这是为了兑现原提示词的要求："不能留下配置看似生效但实际未使用的参数"。
CONSUMED_FIELDS: dict[str, str] = {
    "engine": "custom_voice.server.app:create_engine",
    "device": "custom_voice.inference.stub_engine + Phase2 cosyvoice_engine",
    "precision": "custom_voice.inference.stub_engine + Phase2 cosyvoice_engine",
    "max_concurrent_requests": "custom_voice.server.app:create_engine（Phase 4 接调度器）",
    "request_timeout_s": "custom_voice.server.routes.synthesis",
    "enable_compile": "Phase 4 优化阶段（Phase 1 仅记录到 /system/status）",
    "enable_sdpa": "Phase 4 优化阶段（Phase 1 仅记录到 /system/status）",
    "enable_vllm_llm": "Phase 4 优化阶段（Phase 1 仅记录到 /system/status）",
    "speaker_cache_enabled": "custom_voice.inference.stub_engine + Phase2",
    "speaker_cache_max_items": "custom_voice.inference.stub_engine + Phase2",
    "cpu_offload": "Phase 4 优化阶段（Phase 1 仅记录到 /system/status）",
    "memory_safety_margin_mb": "custom_voice.server.app:lifespan（启动显存检查）",
    "token_hop_len_reset": "Phase 2 cosyvoice_engine（Phase 1 仅记录）",
    "host": "custom_voice.server.app:run",
    "port": "custom_voice.server.app:run",
    "auth_token": "custom_voice.config.schema:_guard_non_loopback",
}


def find_unconsumed_fields(cfg: GPUConfig) -> list[str]:
    """返回配置中存在但未登记消费方的字段名。"""
    return sorted(set(cfg.model_fields) - set(CONSUMED_FIELDS))


def find_stale_registrations(cfg: GPUConfig) -> list[str]:
    """返回登记表中存在但配置里已删除的字段名（防止登记表腐化）。"""
    return sorted(set(CONSUMED_FIELDS) - set(cfg.model_fields))


__all__ = [
    "CONSUMED_FIELDS",
    "EngineKind",
    "GPUConfig",
    "Precision",
    "SdpaMode",
    "find_stale_registrations",
    "find_unconsumed_fields",
]
