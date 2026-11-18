"""配置模型与校验。

设计要点（对应原提示词"不能留下配置看似生效但实际未使用的参数"）：

1. 所有字段都有类型和范围约束，非法值启动即报错。
2. 每个字段必须在 ``CONSUMED_FIELDS`` 中登记"谁消费了它"，
   未登记的字段视为无效配置，启动时报错。
3. 字段名与 ``configs/gpu.yaml`` 一一对应，不允许多余的键。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

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


class TextFrontendMode(str, Enum):
    """文本前端（文本正则化）启用策略。

    背景：wetext 的 __init__ 无条件调 snapshot_download()，每次联网校验
    无 token 时返回 403，导致 CosyVoice 降级到无前端。但 fst 资源其实
    已缓存在本地，故提供本项目的本地加载补丁。

    - auto:   尝试启用本地 wetext，失败则记录警告并降级（默认）
    - wetext: 强制启用，失败则启动报错
    - none:   不启用
    """

    AUTO = "auto"
    WETEXT = "wetext"
    NONE = "none"


class GPUConfig(BaseModel):
    """GPU 与推理配置。

    ``extra='forbid'`` 是关键：配置里写了未定义的键会直接报错，
    防止拼写错误被静默忽略。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        # model_dir 与 pydantic 的 model_ 保护命名空间冲突，
        # 但字段名语义清晰且来自需求，故关闭保护。
        protected_namespaces=(),
    )

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
    max_queue_size: int = Field(
        default=8,
        ge=0,
        le=64,
        description="等待队列上限。超出时返回 429，避免无界堆积。",
    )
    queue_timeout_s: float = Field(
        default=60.0,
        gt=0,
        le=600,
        description="排队超时秒数。超时返回 503。",
    )
    request_timeout_s: float = Field(
        default=120.0,
        gt=0,
        le=600,
        description="单次合成超时秒数。",
    )

    # ---- 流式 ----
    stream_enabled: bool = Field(
        default=True,
        description="是否启用流式合成接口。",
    )
    stream_char_threshold: int = Field(
        default=15,
        ge=1,
        le=200,
        description=(
            "流式路由阈值（有效字符数）。"
            "实测 10 字流式无收益（-4.4%），22 字有收益（+44.8%），取 15。"
        ),
    )

    # ---- 历史记录与日志 ----
    history_retain_days: int = Field(
        default=30,
        ge=0,
        le=3650,
        description=(
            "合成记录保留天数。0 表示不自动清理。"
            "超过此天数的记录会被后台线程定期删除。"
        ),
    )
    log_retain_days: int = Field(
        default=14,
        ge=0,
        le=3650,
        description=(
            "归档日志文件保留天数。0 表示不删除。"
            "只删 zjy14.log.YYYY-MM-DD 形式的归档，不动当前日志。"
        ),
    )
    cleanup_interval_hours: float = Field(
        default=24.0,
        gt=0,
        le=720,
        description="自动清理的间隔小时数。最小值会被钳到 1 分钟。",
    )

    # ---- 优化开关 ----
    # 以下 3 个开关经实测后已移除，理由记录在此避免以后重复添加：
    #   enable_sdpa     PyTorch 2.8 默认已启用 flash/mem_efficient SDPA
    #                   （实测均为 True），CosyVoice 内部也无 sdpa 开关，无作用
    #   enable_vllm_llm 模型目录无 vllm/ 子目录，load_vllm 无可用权重
    #   cpu_offload     CosyVoice3 无 offload 接口；手动 .to('cpu') 会破坏
    #                   推理图，每次请求搬运权重导致延迟灾难性上升
    enable_compile: bool = Field(
        default=False,
        description=(
            "torch.compile 套用到 LLM 子模块。"
            "实测 8 轮交错采样：基线中位 3.049s -> compile 2.783s，"
            "配对差值均值 -0.177s（相对改善 8.7%），8 轮中 7 轮更快。"
            "代价是首次推理触发编译，冷启动敏感场景建议关闭。"
        ),
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

    # ---- 模型与文本前端 ----
    model_dir: str = Field(
        default="",
        description=(
            "CosyVoice3 模型目录。留空则用默认位置 "
            "~/models/Fun-CosyVoice3-0.5B-2512。"
        ),
    )
    text_frontend: TextFrontendMode = Field(
        default=TextFrontendMode.AUTO,
        description=(
            "文本前端策略。auto=尝试启用本地 wetext（失败降级）；"
            "wetext=强制启用（失败报错）；none=不启用。"
        ),
    )
    default_voice_id: str = Field(
        default="elysia",
        description="未指定 voice_id 时使用的默认音色。",
    )
    min_text_ratio: float = Field(
        default=0.5,
        gt=0.0,
        le=2.0,
        description=(
            "合成文本 / 参考文本 的最小长度比。低于此值音色会明显漂移，"
            "实测 0.21 时质心偏移 +490Hz，1.08 时仅 -56Hz。"
            "上游 cosyvoice.py:94 也以 0.5 为警告阈值。"
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
    "device": "custom_voice.inference.stub_engine + cosyvoice_engine",
    "precision": "custom_voice.inference.stub_engine + cosyvoice_engine",
    "max_concurrent_requests": "custom_voice.server.app:lifespan（Scheduler 并发上限）",
    "max_queue_size": "custom_voice.server.app:lifespan（Scheduler 队列上限）",
    "queue_timeout_s": "custom_voice.server.app:lifespan（Scheduler 排队超时）",
    "stream_enabled": "custom_voice.inference.streaming:StreamPolicy",
    "stream_char_threshold": "custom_voice.inference.streaming:StreamPolicy",
    "request_timeout_s": "custom_voice.server.routes.synthesis",
    "enable_compile": "custom_voice.inference.cosyvoice_engine:_apply_compile",
    "speaker_cache_enabled": "custom_voice.inference.stub_engine + cosyvoice_engine",
    "speaker_cache_max_items": "custom_voice.inference.stub_engine + cosyvoice_engine",
    "memory_safety_margin_mb": "custom_voice.server.app:lifespan（启动显存检查）",
    "token_hop_len_reset": "custom_voice.inference.cosyvoice_engine:_reset_hop_len",
    "model_dir": "custom_voice.inference.cosyvoice_engine:load",
    "text_frontend": "custom_voice.inference.cosyvoice_engine:_setup_text_frontend",
    "default_voice_id": "custom_voice.inference.cosyvoice_engine:synthesize",
    "min_text_ratio": "custom_voice.inference.cosyvoice_engine:_check_text_ratio",
    "history_retain_days": "custom_voice.history.cleanup:CleanupScheduler",
    "log_retain_days": "custom_voice.history.cleanup:CleanupScheduler",
    "cleanup_interval_hours": "custom_voice.history.cleanup:CleanupScheduler",
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
    "TextFrontendMode",
    "find_stale_registrations",
    "find_unconsumed_fields",
]
