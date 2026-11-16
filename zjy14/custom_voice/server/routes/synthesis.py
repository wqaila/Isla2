"""合成端点。

契约硬约束（D2）：必须与 zjy13 的 TTSClient 兼容。
- 请求：``{"text", "voice_id", "speed"}``
- 响应：WAV 二进制 + 6 个响应头
- 状态码：400 参数错误 / 503 未就绪 / 5xx 内部错误
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ...core.errors import InvalidRequestError
from ...core.logging import get_logger, get_request_id
from ...schemas.synthesis import SynthesisRequest
from ..deps import get_state

router = APIRouter(tags=["synthesis"])
logger = get_logger("routes.synthesis")


def _record_synthesis(request: Request, req: SynthesisRequest, result) -> None:  # type: ignore[no-untyped-def]
    """把合成记录写入 SQLite。失败不影响主流程。"""
    try:
        state = get_state(request)
        with state.db.transaction() as tx:
            tx.execute(
                "INSERT INTO synthesis_logs ("
                "request_id, text, emotion, style, speed, instruction,"
                "sample_rate, duration_s, total_ms, first_audio_ms, rtf,"
                "peak_vram_mb, engine, created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    get_request_id() or result.request_id,
                    req.text,
                    req.emotion or "",
                    req.style or "",
                    req.speed,
                    req.instruction or "",
                    result.sample_rate,
                    result.duration_s,
                    result.total_ms,
                    result.first_audio_ms,
                    result.rtf,
                    result.peak_vram_mb,
                    result.engine,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
    except Exception:  # noqa: BLE001
        logger.exception("写入合成记录失败（不影响响应）")


@router.post("/synthesize")
async def synthesize(request: Request, payload: SynthesisRequest) -> Response:
    """合成语音，返回 WAV。

    请求体校验失败由 FastAPI 返回 422；这里额外把业务级参数错误转成 400，
    因为 zjy13 的 TTSClient 用 400 判定 ``TTSBadRequestError``。

    **关键实现细节**：合成是同步阻塞调用（几秒），必须用
    ``run_in_threadpool`` 放到线程池执行。
    早期版本直接在 async 函数里调用，会卡死事件循环 ——
    表现为所有请求被 uvicorn 串行化，调度器的排队统计始终为 0，
    并发限制形同虚设（实测 3 并发墙钟 = 单请求耗时）。
    """
    from starlette.concurrency import run_in_threadpool

    state = get_state(request)

    if not state.engine.is_ready():
        from ...core.errors import EngineNotReadyError

        raise EngineNotReadyError("引擎尚未就绪，请稍后重试")

    started = time.perf_counter()
    sched = getattr(state, "scheduler", None)
    req_id = get_request_id() or "unknown"

    def _do_synth():  # type: ignore[no-untyped-def]
        if sched is not None:
            return sched.run(
                req_id,
                lambda: state.engine.synthesize(payload),
                timeout_s=state.config.queue_timeout_s,
            )
        return state.engine.synthesize(payload)

    # 放到线程池，避免阻塞事件循环
    result = await run_in_threadpool(_do_synth)
    result.request_id = get_request_id()
    elapsed_ms = (time.perf_counter() - started) * 1000

    _record_synthesis(request, payload, result)

    logger.info(
        "合成完成",
        extra={
            "extra_fields": {
                "voice_id": payload.voice_id,
                "chars": len(payload.text),
                "duration_s": round(result.duration_s, 3),
                "total_ms": round(elapsed_ms, 1),
                "rtf": round(result.rtf, 3),
                "engine": result.engine,
            }
        },
    )

    return Response(
        content=result.wav_bytes,
        media_type="audio/wav",
        headers=result.response_headers(),
    )


@router.post("/api/v1/synthesis")
async def synthesize_v1(request: Request, payload: SynthesisRequest) -> Response:
    """带版本前缀的同一接口，便于后续演进。"""
    return await synthesize(request, payload)


# ----------------------------------------------------------------------
# 流式接口
# ----------------------------------------------------------------------


@router.post("/synthesize_stream")
async def synthesize_stream(request: Request, payload: SynthesisRequest):
    """流式合成：边生成边推 PCM 分块。

    传输协议**完全复用 zjy13 已验证的格式**（见 zjy13/scripts/tts_server_wsl.py）：

        每块 = 4 字节大端 uint32 长度 + 该长度的裸 PCM S16LE 单声道数据
        末尾发送一个长度 = 0 的块表示结束

    采样率通过响应头 ``x-sample-rate`` 告知（24000）。

    为什么不用 SSE / chunked JSON：
      - 音频是二进制，Base64 会增加 33% 体积
      - 长度前缀无歧义，客户端解析简单，断流时能明确检测（缺结束标记）

    失败处理：
      - 首块之前出错 -> 抛异常转成 JSON 错误响应
      - 中途出错 -> 直接断开连接，**不发结束标记**，
        让调用方明确知道流不完整，而不是静默截断。

    长短句路由：本接口不做路由判断，调用方应先用
    ``StreamPolicy.should_stream()`` 决定（短句流式无收益）。
    """
    from fastapi.responses import StreamingResponse

    from ...inference.streaming import StreamPolicy, describe_routing

    state = get_state(request)

    if not state.engine.is_ready():
        from ...core.errors import EngineNotReadyError

        raise EngineNotReadyError("引擎尚未就绪，请稍后重试")

    req_id = get_request_id()
    routing = describe_routing(payload.text, StreamPolicy())

    logger.info(
        "流式合成开始",
        extra={
            "extra_fields": {
                "request_id": req_id,
                "chars": routing["chars"],
                "mode": routing["mode"],
                "voice_id": payload.voice_id,
            }
        },
    )

    def generate():
        import struct

        started = time.perf_counter()
        first_ms: float | None = None
        total_samples = 0
        n_chunks = 0

        # 流式也必须受并发限制约束，否则会和非流式请求抢显存。
        # 注意：这里用「占用槽位直到流结束」的语义 ——
        # 整个流式传输期间都占着一个并发名额。
        sched = getattr(state, "scheduler", None)
        acquired = False
        if sched is not None:
            # 直接操作信号量而不走 run()，因为 run() 要求 fn 返回后才释放，
            # 而流式需要在整个生成期间持有。
            acquired = sched._sem.acquire(timeout=state.config.queue_timeout_s)  # noqa: SLF001
            if not acquired:
                logger.warning(
                    "流式请求排队超时",
                    extra={"extra_fields": {"request_id": req_id}},
                )
                yield struct.pack(">I", 0)
                return

        try:
            for chunk in state.engine.synthesize_stream(payload):
                if chunk.is_final:
                    break
                if not chunk.pcm:
                    continue
                if first_ms is None:
                    first_ms = (time.perf_counter() - started) * 1000
                total_samples += len(chunk.pcm) // 2
                n_chunks += 1
                yield struct.pack(">I", len(chunk.pcm)) + chunk.pcm

            # 结束标记：长度 0
            yield struct.pack(">I", 0)

            duration = total_samples / 24000
            total_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "流式合成完成",
                extra={
                    "extra_fields": {
                        "request_id": req_id,
                        "voice_id": payload.voice_id,
                        "chars": len(payload.text),
                        "duration_s": round(duration, 3),
                        "first_ms": round(first_ms, 1) if first_ms else -1,
                        "total_ms": round(total_ms, 1),
                        "chunks": n_chunks,
                        "rtf": round((total_ms / 1000) / duration, 3)
                        if duration > 0
                        else 0,
                    }
                },
            )
        except Exception:  # noqa: BLE001
            # 不发送结束标记：让调用方明确知道流不完整
            logger.exception(
                "流式合成失败（已发送部分数据）",
                extra={"extra_fields": {"request_id": req_id}},
            )
            raise
        finally:
            # 无论成功失败，都必须释放并发槽位，否则后续请求会永久卡死
            if acquired and sched is not None:
                sched._sem.release()  # noqa: SLF001

    return StreamingResponse(
        generate(),
        media_type="application/octet-stream",
        headers={
            "x-sample-rate": "24000",
            "x-engine": "cosyvoice3",
            "x-voice-id": payload.voice_id,
            "x-request-id": req_id,
            "x-stream-protocol": "len32be-prefixed-pcm-s16le",
        },
    )


@router.post("/api/v1/synthesis/stream")
async def synthesize_stream_v1(request: Request, payload: SynthesisRequest):
    """带版本前缀的流式接口。"""
    return await synthesize_stream(request, payload)


@router.post("/api/v1/synthesis/{request_id}/cancel")
async def cancel_synthesis(request: Request, request_id: str) -> dict:
    """取消一个进行中的合成请求。

    说明：当前实现标记取消，正在执行的请求会在下一个分块检查点退出。
    由于单次合成通常几秒内完成，实际生效窗口较短。
    """
    state = get_state(request)
    sched = getattr(state, "scheduler", None)
    if sched is None:
        return {"ok": False, "reason": "调度器未启用"}
    sched.cancel(request_id)
    return {"ok": True, "request_id": request_id}
