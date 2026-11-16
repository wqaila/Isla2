"""设备端音频下发：把引擎流式输出适配成 zjy13 协议帧序列。

这一层对应 zjy13 的 `server/ws/server.py:_stream_tts_audio`，
但不重复实现 WebSocket —— WebSocket 服务仍在 zjy13，
zjy14 只负责「引擎 → 协议帧」这一段。

职责边界（决策 D7 范围收敛）：
  zjy14 做：引擎、音频适配、帧切分、背压计算
  zjy13 做：WebSocket 连接、设备会话、ASR/LLM 编排、固件通信

这样划分的好处：
  - 不重写已验证的固件与协议
  - zjy14 可独立测试（不需要真设备）
  - 将来若换传输层（如 MQTT），只需替换 zjy13 侧
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterator

from ..core.logging import get_logger
from .protocol import (
    BYTES_PER_SEC,
    DEFAULT_FRAME_BYTES,
    BackpressureState,
    FrameStats,
    encode_length_prefixed,
    encode_stream_end,
    iter_frames,
)

logger = get_logger("bridge.stream")


@dataclass(slots=True)
class StreamResult:
    """一次流式下发的结果。"""

    frames: int
    total_bytes: int
    audio_s: float
    first_frame_ms: float
    total_ms: float
    frame_stats: FrameStats = field(default_factory=FrameStats)
    backpressure: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "frames": self.frames,
            "total_bytes": self.total_bytes,
            "audio_s": round(self.audio_s, 3),
            "first_frame_ms": round(self.first_frame_ms, 1),
            "total_ms": round(self.total_ms, 1),
            "rtf": round(self.total_ms / 1000 / self.audio_s, 3)
            if self.audio_s > 0
            else 0.0,
            "frame_stats": self.frame_stats.to_dict(),
            "backpressure": self.backpressure,
        }


def stream_to_protocol_frames(
    engine,  # noqa: ANN001
    request,  # noqa: ANN001
    *,
    frame_bytes: int = DEFAULT_FRAME_BYTES,
    turn_id_short: int = 0,
    simulate_playback: bool = True,
) -> tuple[list[bytes], StreamResult]:
    """把引擎的流式输出转成协议帧。

    :param engine: 实现 TTSEngine 协议的引擎
    :param request: SynthesisRequest
    :param frame_bytes: 每帧字节数，默认 4096（≈128ms）
    :param turn_id_short: 轮次短 ID
    :param simulate_playback: 是否模拟播放进度以计算背压等待
    :return: (帧列表, 结果统计)
    """
    started = time.perf_counter()
    first_frame_ms: float | None = None

    # 先收集引擎的 PCM 块，同时记录首块到达时间。
    # 注意：首帧延迟 = 引擎产出第一块的时间 + 切帧耗时，
    # 不是整段收集完的时间（早期版本写错了，导致首帧延迟 == 总耗时）。
    pcm_chunks: list[bytes] = []
    engine_first_chunk_ms: float | None = None
    for chunk in engine.synthesize_stream(request):
        if chunk.is_final:
            break
        if chunk.pcm:
            if engine_first_chunk_ms is None:
                engine_first_chunk_ms = (time.perf_counter() - started) * 1000
            pcm_chunks.append(chunk.pcm)

    if not pcm_chunks:
        logger.warning("引擎未产出任何音频")
        return [], StreamResult(
            frames=0, total_bytes=0, audio_s=0.0,
            first_frame_ms=-1.0, total_ms=0.0,
        )

    # 切成协议帧
    frames, stats = iter_frames(
        pcm_chunks, frame_bytes=frame_bytes, turn_id_short=turn_id_short
    )

    # 首帧可用时间 = 引擎首块到达时间 + 切分这一帧的耗时可忽略，
    # 因此直接取引擎首块时间。这样指标才有意义：
    # 它反映"设备多久能拿到第一帧"，而不是"整段合成完多久"。
    if frames:
        first_frame_ms = engine_first_chunk_ms

    # 背压：模拟播放进度，统计需要等待的总时长
    bp = BackpressureState()
    total_wait = 0.0
    if simulate_playback:
        played = 0.0
        for f in frames:
            payload_len = len(f) - 12
            wait = bp.should_wait(played, payload_len)
            total_wait += wait
            bp.mark_sent(payload_len)
            # 假设播放器以实时速率消费
            played += payload_len / BYTES_PER_SEC
    else:
        for f in frames:
            bp.mark_sent(len(f) - 12)

    total_ms = (time.perf_counter() - started) * 1000
    audio_s = stats.total_bytes / BYTES_PER_SEC

    result = StreamResult(
        frames=stats.frames,
        total_bytes=stats.total_bytes,
        audio_s=audio_s,
        first_frame_ms=first_frame_ms or -1.0,
        total_ms=total_ms,
        frame_stats=stats,
        backpressure={
            **bp.to_dict(),
            "simulated_wait_s": round(total_wait, 2),
            "note": "等待时长用于验证背压逻辑；实际下发由 zjy13 按真实播放进度节流",
        },
    )

    logger.info(
        "流式下发完成",
        extra={"extra_fields": result.to_dict()},
    )
    return frames, result


def stream_to_http_format(
    engine,  # noqa: ANN001
    request,  # noqa: ANN001
) -> Iterator[bytes]:
    """把引擎输出转成 zjy14 HTTP 流式格式（4 字节长度前缀）。

    这是给 `TTSClient.synthesize_stream` 消费的格式，
    与 zjy13 的 `/synthesize_stream` 完全一致。
    """
    for chunk in engine.synthesize_stream(request):
        if chunk.is_final:
            break
        if chunk.pcm:
            yield encode_length_prefixed(chunk.pcm)
    yield encode_stream_end()


class FakeDevice:
    """模拟 ESP32 设备：接收协议帧并按固件逻辑消费。

    模拟固件行为（依据 zjy13 firmware/main/app_config.h）：
      - 200ms 预缓冲（APP_PLAYBACK_RING_MS）
      - 攒够预缓冲才"开播"
      - 检测 seq 连续性
      - 空负载帧 = 结束标记
    """

    def __init__(self, ring_ms: int = 200) -> None:
        self.ring_bytes = BYTES_PER_SEC * ring_ms // 1000
        self.buffer = bytearray()
        self.played_bytes = 0
        self.frames = 0
        self.seq_errors: list[str] = []
        self.started = False
        self.start_at_frame: int | None = None
        self.got_end = False
        self._expected_seq: int | None = None

    def feed(self, frame: bytes) -> None:
        """喂入一帧（完整的 12 字节头 + 负载）。"""
        from .protocol import decode_binary_frame

        try:
            d = decode_binary_frame(frame)
        except ValueError as exc:
            self.seq_errors.append(f"解码失败: {exc}")
            return

        self.frames += 1
        seq = d["seq"]

        # 序号连续性检查（uint16 回绕）
        if self._expected_seq is not None and seq != self._expected_seq:
            self.seq_errors.append(
                f"序号跳变: 期望 {self._expected_seq}，收到 {seq}"
            )
        self._expected_seq = (seq + 1) & 0xFFFF

        payload = d["payload"]
        if not payload:
            self.got_end = True
            return

        self.buffer.extend(payload)

        # 攒够预缓冲就"开播"
        if not self.started and len(self.buffer) >= self.ring_bytes:
            self.started = True
            self.start_at_frame = self.frames

    def drain(self) -> None:
        """模拟播放：清空缓冲，累计已播放字节。"""
        self.played_bytes += len(self.buffer)
        self.buffer.clear()

    def summary(self) -> dict:
        return {
            "frames": self.frames,
            "got_end": self.got_end,
            "started": self.started,
            "start_at_frame": self.start_at_frame,
            "played_bytes": self.played_bytes,
            "played_s": round(self.played_bytes / BYTES_PER_SEC, 3),
            "pending_bytes": len(self.buffer),
            "seq_errors": self.seq_errors,
        }


__all__ = [
    "FakeDevice",
    "StreamResult",
    "stream_to_http_format",
    "stream_to_protocol_frames",
]
