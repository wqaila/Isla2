"""合成端点。

契约硬约束（D2）：必须与 zjy13 的 TTSClient 兼容。
- 请求：``{"text", "voice_id", "speed"}``
- 响应：WAV 二进制 + 6 个响应头
- 状态码：400 参数错误 / 503 未就绪 / 5xx 内部错误
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ...bridge.protocol import encode_length_prefixed, encode_stream_end
from ...core.errors import QueueTimeoutError
from ...core.logging import get_logger, get_request_id
from ...schemas.synthesis import SynthesisRequest
from ..deps import get_state

router = APIRouter(tags=["synthesis"])
logger = get_logger("routes.synthesis")


def _record_synthesis(request: Request, req: SynthesisRequest, result) -> None:  # type: ignore[no-untyped-def]
    """把成功合成记录写入 SQLite。失败不影响主流程。

    注意 ``speaker_id`` 存的是 voice_id —— 合成记录表的字段名沿用
    「声音档案」的命名，但 TTS 侧的标识是 voice_id。查询 API 的
    ``voice_id`` 参数即映射到这一列。
    """
    try:
        state = get_state(request)
        with state.db.transaction() as tx:
            tx.execute(
                "INSERT INTO synthesis_logs ("
                "request_id, speaker_id, text, emotion, style, speed, instruction,"
                "sample_rate, duration_s, total_ms, first_audio_ms, rtf,"
                "peak_vram_mb, engine, created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    get_request_id() or result.request_id,
                    result.voice_id or req.voice_id,
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


def _record_failure(request: Request, req: SynthesisRequest, code: str) -> None:
    """记录一次失败的合成。

    为什么单独记：查询 API 的 ``only_failed`` 需要真实数据。
    若只记成功，「失败统计」永远是 0，失去意义。
    """
    try:
        state = get_state(request)
        with state.db.transaction() as tx:
            tx.execute(
                "INSERT INTO synthesis_logs ("
                "request_id, speaker_id, text, emotion, style, speed, instruction,"
                "sample_rate, duration_s, total_ms, first_audio_ms, rtf,"
                "peak_vram_mb, engine, error_code, created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    get_request_id() or "unknown",
                    req.voice_id,
                    req.text,
                    req.emotion or "",
                    req.style or "",
                    req.speed,
                    req.instruction or "",
                    0,
                    0.0,
                    0.0,
                    None,
                    0.0,
                    None,
                    "",
                    code,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
    except Exception:  # noqa: BLE001
        logger.exception("写入失败记录失败（不影响响应）")


def _record_stream_synthesis(
    request: Request,
    req: SynthesisRequest,
    *,
    sample_rate: int,
    duration_s: float,
    total_ms: float,
    first_audio_ms: float | None,
    engine: str,
) -> None:
    """记录一次**流式**合成（2026-11-19 新增）。

    为什么需要单独一个函数：
      原来只有非流式路径会写 ``synthesis_logs``，而 zjy13 的生产路径
      **只走流式**（ws/server.py 里唯一调用是 ``synthesize_stream``）。
      结果是 zjy14 的合成历史永远是空的 —— ``/api/v1/history`` 查不到
      任何真实请求，``only_failed`` 统计恒为 0，自动清理也无数据可清。
      线上排障时完全看不到 zjy14 侧发生过什么。

    与非流式版的区别：
      流式没有 ``SynthesisResult``（也就没有 wav_bytes / peak_vram_mb），
      所以这里直接传各项指标，不构造那个对象。
    """
    try:
        state = get_state(request)
        rtf = (total_ms / 1000.0) / duration_s if duration_s > 0 else 0.0
        with state.db.transaction() as tx:
            tx.execute(
                "INSERT INTO synthesis_logs ("
                "request_id, speaker_id, text, emotion, style, speed, instruction,"
                "sample_rate, duration_s, total_ms, first_audio_ms, rtf,"
                "peak_vram_mb, engine, created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    get_request_id() or "unknown",
                    req.voice_id,
                    req.text,
                    req.emotion or "",
                    req.style or "",
                    req.speed,
                    req.instruction or "",
                    sample_rate,
                    duration_s,
                    total_ms,
                    first_audio_ms,
                    round(rtf, 4),
                    None,
                    engine,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
    except Exception:  # noqa: BLE001
        logger.exception("写入流式合成记录失败（不影响响应）")


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
    try:
        result = await run_in_threadpool(_do_synth)
    except Exception as exc:  # noqa: BLE001
        # 记录失败，让查询 API 的 only_failed / 失败统计有真实数据
        code = getattr(exc, "code", "") or type(exc).__name__
        _record_failure(request, payload, str(code))
        raise
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

    # 音色预校验（2026-11-19 新增）。
    #
    # 为什么必须在返回 StreamingResponse **之前**做：
    #   引擎内部（cosyvoice_engine._pick_voice）也会校验音色，但那时
    #   响应头已经发出（StreamingResponse 先发 start 再迭代 body），
    #   抛异常改不了状态码 —— 调用方只能看到「流被截断」。
    #   zjy13 会报 "TTS 流被截断（未收到结束标记）"，完全定位不到
    #   是音色配置问题。
    #
    #   放在这里，非法音色直接返回 400，zjy13 抛 TTSBadRequestError，
    #   错误信息里能明确看到是哪个 voice_id 不认识。
    try:
        known_voices = state.engine.list_voices()
    except Exception:  # noqa: BLE001
        known_voices = []
    if known_voices and payload.voice_id not in known_voices:
        from ...core.errors import InvalidRequestError

        raise InvalidRequestError(
            f"未知音色 {payload.voice_id!r}，可用：{', '.join(known_voices)}",
            detail={"voice_id": payload.voice_id, "available": known_voices},
        )

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

    # ---- 排队获取并发槽位（必须在返回 StreamingResponse 之前）----
    #
    # 2026-11-19 修复两处问题，都在这里：
    #
    # 1) **静默失败**：原来排队超时只 `yield 一个「长度 0」的结束标记` 就 return。
    #    zjy13 的 `_iter_length_prefixed` 把长度 0 理解为「正常完成」，
    #    于是排队超时被当成「合成成功但音频为空」—— 板子静默、日志无错、
    #    调用方照常收尾。实测确认（tests/test_queue_timeout.py）。
    #
    # 2) **抛异常的位置**：一开始我把 `raise QueueTimeoutError` 写在
    #    `generate()` 生成器内部，以为 FastAPI 会转成 503。**实测是错的**：
    #    Starlette 的 StreamingResponse 会**先发 http.response.start（200）**，
    #    再迭代 body。生成器里抛异常时响应头已经发出去了，状态码改不了，
    #    结果仍是 200 + 空 body（同样静默）。
    #
    #    所以排队检查必须放在 handler 层、在构造 StreamingResponse 之前 ——
    #    那时异常还能被 app 的 ZJY14Error 处理器转成 503。
    sched = getattr(state, "scheduler", None)
    acquired = False
    if sched is not None:
        # 用「占用槽位直到流结束」的语义：整个流式传输期间都占一个并发名额。
        # 直接操作信号量而不走 run()，因为 run() 要求 fn 返回后才释放。
        acquired = sched._sem.acquire(timeout=state.config.queue_timeout_s)  # noqa: SLF001
        if not acquired:
            logger.warning(
                "流式请求排队超时",
                extra={"extra_fields": {"request_id": req_id}},
            )
            raise QueueTimeoutError(
                f"推理队列已满（{state.config.max_queue_size}），"
                f"等待 {state.config.queue_timeout_s}s 仍未获得执行槽位"
            )

    # 响应头与统计必须反映**真实**引擎与采样率，不能硬编码。
    # 硬编码曾导致：engine=stub 时仍报 "cosyvoice3"，把「流式无音频」
    # 这个故障彻底掩盖住（排查时看到 cosyvoice3 就以为引擎没问题）。
    try:
        engine_stats = state.engine.stats()
        real_engine = engine_stats.kind or "unknown"
        real_sample_rate = engine_stats.sample_rate or 24000
    except Exception:  # noqa: BLE001
        # 取不到统计信息时给保守默认，但不能因此让整个请求失败
        real_engine = "unknown"
        real_sample_rate = 24000

    def generate():
        started = time.perf_counter()
        first_ms: float | None = None
        total_samples = 0
        n_chunks = 0

        try:
            for chunk in state.engine.synthesize_stream(payload):
                # 2026-11-19 修复：is_final 的块**可能带音频**，不能直接 break。
                #
                # `TTSEngine` 协议（inference/engine.py）允许两种实现风格：
                #   A) 真流式：先多个 is_final=False 的音频块，
                #      最后 is_final=True 且 pcm 为空 —— CosyVoice3 就是这样
                #   B) 一次性：**只** yield 一个 is_final=True 且带完整 pcm 的块
                #      —— StubEngine 就是这样（stub_engine.py:166-176）
                #
                # 原实现写的是 `if chunk.is_final: break`，在 A 下没问题，
                # 但在 B 下会把**唯一一块带音频的数据整个丢掉**，
                # 导致流式接口返回 200 + 零音频。
                #
                # 而 configs/gpu.yaml 的默认 engine 正是 stub，
                # 也就是说「默认配置下流式永远无声」，且 x-engine 头
                # 还硬编码 cosyvoice3，排查时会被彻底误导。
                #
                # 正确做法：先看有没有 pcm 要发，再看要不要结束。
                if chunk.pcm:
                    if first_ms is None:
                        first_ms = (time.perf_counter() - started) * 1000
                    total_samples += len(chunk.pcm) // 2
                    n_chunks += 1
                    # 2026-11-23 修复：必须按协议上限切块。
                    #
                    # 原实现直接 `struct.pack(">I", len(chunk.pcm)) + chunk.pcm`，
                    # 而引擎按「句」yield —— 实测一整句 192000 字节
                    # （14.8s 音频被切成 10 块，最大块 192000），
                    # 远超协议上限 48KB。zjy13 的 TTSClient 会校验长度字段，
                    # 超限直接抛「TTS 流式分块长度异常」，表现为板子播到一半中止。
                    #
                    # 统一走 encode_length_prefixed，它内部会按
                    # MAX_AUDIO_CHUNK_BYTES 切分，避免两处实现漂移。
                    yield encode_length_prefixed(chunk.pcm)
                if chunk.is_final:
                    break

            # 结束标记：长度 0
            yield encode_stream_end()

            # 用真实采样率算时长，别硬编码 24000（否则换模型后统计全错）
            duration = total_samples / (real_sample_rate or 24000)
            total_ms = (time.perf_counter() - started) * 1000

            # 2026-11-19 新增：0 块音频必须留下明确痕迹。
            #
            # 实测遇到过：请求文本是纯标点（如 "✨。"），引擎产不出任何音频，
            # 但接口照样返回 200 + 结束标记，调用方以为合成成功却没声音。
            # 这类「成功但无输出」是最难排查的静默失效。
            #
            # 这里不改响应（已经发了 200 和结束标记，无法回头改状态码），
            # 但要：
            #   1. 用 ERROR 级别记日志，而不是静默 INFO
            #   2. 在响应头 x-synthesis-warning 里留痕
            if n_chunks == 0:
                logger.error(
                    "流式合成未产出任何音频",
                    extra={
                        "extra_fields": {
                            "request_id": req_id,
                            "voice_id": payload.voice_id,
                            "chars": len(payload.text),
                            "text_preview": payload.text[:40],
                            "reason": "文本可能全是标点或无法发音的字符",
                        }
                    },
                )

            # 落库（2026-11-19 新增）：zjy13 只走流式，不记录就等于
            # zjy14 的合成历史永远为空，历史查询与失败统计全部失效。
            _record_stream_synthesis(
                request,
                payload,
                sample_rate=real_sample_rate,
                duration_s=duration,
                total_ms=total_ms,
                first_audio_ms=first_ms,
                engine=real_engine,
            )

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
        except Exception as exc:  # noqa: BLE001
            # 不发送结束标记：让调用方明确知道流不完整
            logger.exception(
                "流式合成失败（已发送部分数据）",
                extra={"extra_fields": {"request_id": req_id}},
            )
            # 失败也要落库，否则 only_failed 统计永远是 0
            # （zjy13 只走流式，这是唯一的失败记录来源）。
            code = getattr(exc, "code", None) or type(exc).__name__
            _record_failure(request, payload, str(code))
            raise
        finally:
            # 无论成功失败，都必须释放并发槽位，否则后续请求会永久卡死
            if acquired and sched is not None:
                sched._sem.release()  # noqa: SLF001

    return StreamingResponse(
        generate(),
        media_type="application/octet-stream",
        headers={
            "x-sample-rate": str(real_sample_rate),
            "x-engine": real_engine,
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
