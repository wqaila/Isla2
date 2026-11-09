"""TTS 引擎抽象。

Phase 1 只定义协议与 Stub 实现；Phase 2 写 ``CosyVoiceEngine`` 实现同一协议。
路由层只依赖本模块的 ``TTSEngine``，**绝不 import cosyvoice**。

这样 Phase 2 接入真模型时，路由、测试、日志全部不动，只换实现类。
"""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from ..schemas.synthesis import (
    AudioChunk,
    EngineStats,
    SynthesisRequest,
    SynthesisResult,
)


@runtime_checkable
class TTSEngine(Protocol):
    """引擎协议。

    实现类必须保证：
    - ``load()`` 可重复调用而不重复加载
    - ``synthesize()`` 在未就绪时抛 ``EngineNotReadyError``
    - 参数非法时抛 ``InvalidRequestError``
    """

    def load(self) -> None:
        """加载模型。幂等。"""
        ...

    def is_ready(self) -> bool:
        """模型是否已就绪。"""
        ...

    def list_voices(self) -> list[str]:
        """可用音色列表。"""
        ...

    def synthesize(self, req: SynthesisRequest) -> SynthesisResult:
        """合成整段音频。"""
        ...

    def synthesize_stream(self, req: SynthesisRequest) -> Iterator[AudioChunk]:
        """流式合成。

        Phase 1 的实现一次性 yield 一个 ``is_final=True`` 的块。
        Phase 4 换成真流式，**接口不变**。
        """
        ...

    def stats(self) -> EngineStats:
        """引擎状态快照。"""
        ...

    def shutdown(self) -> None:
        """释放资源。"""
        ...


__all__ = ["TTSEngine"]
