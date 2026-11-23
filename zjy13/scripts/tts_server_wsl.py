"""CosyVoice3 本地 TTS HTTP 服务（运行在 WSL2）。

设计要点（对应 V2 第六节）：
- 只监听 127.0.0.1，不对外暴露（V2 第 126 行）
- 模型只加载一次，启动时预热（避免首句卡顿）
- 参考音频编码缓存（实测省 16.5%）
- 每次合成前重置 token_hop_len（修复上游污染 bug）
- 返回 WAV 二进制，头部带元数据（采样率、时长、引擎）

启动：
    wsl -e bash /mnt/d/xiangmudaima/zjy13/scripts/serve_tts_wsl.sh
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import struct
import sys
import time
import wave
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

COSYVOICE_DIR = Path.home() / "CosyVoice"
MODEL_DIR = Path.home() / "models" / "Fun-CosyVoice3-0.5B-2512"

# 默认音色：不再使用 CosyVoice 自带示例音频，只用角色专属音色。
# 参考音频来自 server/characters/voices/<voice_id>/。
DEFAULT_VOICE_ID = os.environ.get("TTS_VOICE_ID", "elysia")

# CosyVoice3 的参考文本必须带这个前缀分隔符。
# 实测教训：只写纯文本（不加前缀）时，注册能成功、健康检查也正常，
# 但每次合成都在卷积层报
#   RuntimeError: Calculated padded input size per channel: (3).
#   Kernel size: (4). Kernel size can't be greater than actual input size
# 原因是 LLM 前缀错位后生成阶段拿到过短的 token 序列。
# 官方所有示例（example.py / vllm_example.py）的 prompt_text 都带此前缀。
PROMPT_PREFIX = "You are a helpful assistant.<|endofprompt|>"

# 角色音色注册表：voice_id -> (参考音频, 参考文本)。
# 参考音频由 tools/prepare_voice_ref.py 从角色语音素材中挑选并归一化导出，
# 放在 Windows 侧的 server/characters/voices/<voice_id>/ 下，
# 通过 /mnt/d/... 路径在 WSL 里访问。
VOICES_DIR = Path(os.environ.get(
    "TTS_VOICES_DIR",
    "/mnt/d/xiangmudaima/zjy13/server/characters/voices",
))

# 流式下发时单个 PCM 块的最大字节数。
#
# 必须 ≤ 协议上限（server/protocol/messages.py 的 MAX_AUDIO_CHUNK_BYTES = 48KB）。
# 本脚本在 WSL 里运行、只依赖 numpy/torch，不 import server 包，
# 所以这里显式复制该值；改协议时请同步（tests 里有断言覆盖两者关系）。
MAX_STREAM_CHUNK_BYTES = 48 * 1024

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("cosyvoice.server")


@dataclass
class VoiceProfile:
    """一个可用的音色。"""

    voice_id: str
    ref_wav: Path
    ref_text: str
    is_builtin: bool = False

    def to_dict(self) -> dict:
        return {
            "voice_id": self.voice_id,
            "ref_wav": str(self.ref_wav),
            "is_builtin": self.is_builtin,
        }


def discover_voices() -> dict[str, VoiceProfile]:
    """扫描音色目录，返回 voice_id -> VoiceProfile。

    约定：每个音色一个子目录，内含 ``ref_*.wav`` 与同名 ``.txt``。
    文本缺失时该音色不可用 —— 零样本克隆没有参考文本会明显跑偏，
    宁可报错也不要悄悄用空文本合成。
    """
    voices: dict[str, VoiceProfile] = {}

    if not VOICES_DIR.is_dir():
        logger.error("音色目录不存在: %s", VOICES_DIR)
        return voices

    for sub in sorted(VOICES_DIR.iterdir()):
        if not sub.is_dir():
            continue
        wavs = sorted(sub.glob("ref_*.wav")) or sorted(sub.glob("*.wav"))
        if not wavs:
            logger.warning("音色 %s 缺少参考音频，已跳过", sub.name)
            continue
        wav = wavs[0]
        txt = wav.with_suffix(".txt")
        if not txt.is_file():
            logger.warning("音色 %s 缺少参考文本 %s，已跳过", sub.name, txt.name)
            continue
        text = txt.read_text(encoding="utf-8").strip()
        if not text:
            logger.warning("音色 %s 的参考文本为空，已跳过", sub.name)
            continue
        # 补上 CosyVoice3 必需的分隔符前缀（纯文本会导致合成阶段卷积报错）
        if "<|endofprompt|>" not in text:
            text = PROMPT_PREFIX + text
        voices[sub.name] = VoiceProfile(
            voice_id=sub.name, ref_wav=wav, ref_text=text, is_builtin=False
        )
        logger.info("发现音色 %s -> %s", sub.name, wav.name)

    return voices


@dataclass
class Runtime:
    """全局运行时状态。"""

    model: object = None
    sample_rate: int = 24000
    load_s: float = 0.0
    warmup_s: float = 0.0
    prompt_cached: bool = False
    device: str = ""
    torch_version: str = ""
    gpu_name: str = ""
    arch_match: bool = False
    voices: dict[str, VoiceProfile] = field(default_factory=dict)


runtime = Runtime()


def _patch_wetext_offline() -> None:
    """让 wetext 文本前端走本地 fst，不再联网下载。

    背景（2026-11-09 实测）：
    CosyVoice 的 ``frontend.py`` 用 try/except 依次尝试 ttsfrd → wetext → 无前端。
    wetext 的 ``Normalizer()`` 在不传路径时**必定调用**
    ``snapshot_download("pengzhendong/wetext")``，这需要 ModelScope 鉴权。
    本机没有 token，于是抛 ``HTTPError: Authentication token does not exist``，
    该异常又被外层 bare ``except`` 吞掉，最终 ``text_frontend = ''``——
    **服务能启动，但中文数字不会被正则化**（"128" 读成 "一二八" 而不是"一百二十八"）。

    更糟的是：``snapshot_download`` 会先尝试联网鉴权，在弱网下表现为
    「启动卡在 Downloading Model ... 很久」，看起来像在下模型，实际是在等鉴权。

    模型文件本身是完整的（53 项 / 66MB，已验证）。
    ``Normalizer`` 支持直接传 ``tagger_path`` / ``verbalizer_path``，
    这样完全跳过 ``snapshot_download``：实测加载 **0.02s**，不联网。

    实现：给 ``wetext.Normalizer`` 套一层，把 None 的路径参数替换成默认缓存里的
    本地 fst。CosyVoice 那边 ``ZhNormalizer(remove_erhua=False)`` 与
    ``EnNormalizer()`` 都保持原样调用，无需改第三方代码。
    """
    try:
        import wetext
    except Exception as exc:  # noqa: BLE001
        logger.warning("wetext 未安装，文本前端将不可用: %s", exc)
        return

    base = Path(os.environ.get(
        "WETEXT_MODEL_DIR",
        str(Path.home() / ".cache" / "modelscope" / "hub" / "pengzhendong" / "wetext"),
    ))

    zh_tagger = base / "zh" / "tn" / "tagger.fst"
    zh_verbalizer = base / "zh" / "tn" / "verbalizer.fst"
    en_tagger = base / "en" / "tn" / "tagger.fst"
    en_verbalizer = base / "en" / "tn" / "verbalizer.fst"

    if not (zh_tagger.is_file() and zh_verbalizer.is_file()):
        logger.warning(
            "本地 wetext 模型不完整（%s），文本前端仍会尝试联网下载", base
        )
        return

    original = wetext.Normalizer

    class OfflineNormalizer(original):  # type: ignore[misc, valid-type]
        """路径参数缺省时自动填本地 fst，避免 snapshot_download。

        约束（读 wetext 源码得知）：
        一旦传了 ``tagger_path`` / ``verbalizer_path``，``lang`` 就必须是
        ``"en"`` 或 ``"zh"``，不能是默认的 ``"auto"``——否则触发
        ``AssertionError: Language must be 'en' or 'zh'``。

        CosyVoice 的 ``frontend.py`` 是这么调的：
            self.zh_tn_model = ZhNormalizer(remove_erhua=False)   # 中文
            self.en_tn_model = EnNormalizer()                      # 英文
        两次调用都不带 lang，靠这里的推断区分：
        第一次（带 remove_erhua）判为 zh，第二次判为 en。
        """

        def __init__(self, *args, **kwargs):
            tagger = kwargs.get("tagger_path")
            verbalizer = kwargs.get("verbalizer_path")
            if len(args) >= 1 and tagger is None:
                tagger = args[0]
            if len(args) >= 2 and verbalizer is None:
                verbalizer = args[1]

            if tagger is None or verbalizer is None:
                lang = kwargs.get("lang", "auto")
                # 带 remove_erhua / enable_0_to_9 的只可能是中文调用
                zh_hint = ("remove_erhua" in kwargs) or ("enable_0_to_9" in kwargs)
                if lang in ("auto", ""):
                    lang = "zh" if zh_hint else "en"
                elif lang not in ("zh", "en"):
                    lang = "zh" if zh_hint else "en"

                if lang == "zh":
                    t, v = zh_tagger, zh_verbalizer
                else:
                    t, v = en_tagger, en_verbalizer

                if tagger is None:
                    kwargs["tagger_path"] = str(t)
                if verbalizer is None:
                    kwargs["verbalizer_path"] = str(v)
                kwargs["lang"] = lang

            super().__init__(*args, **kwargs)

    wetext.Normalizer = OfflineNormalizer
    logger.info("wetext 文本前端已切到本地模型: %s", base)


def load_model() -> None:
    """加载模型、缓存参考音频、预热。"""
    # 必须先切到仓库目录并加入 sys.path，否则 import cosyvoice 会失败
    os.chdir(COSYVOICE_DIR)
    repo = str(COSYVOICE_DIR)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    matcha = str(COSYVOICE_DIR / "third_party" / "Matcha-TTS")
    if matcha not in sys.path:
        sys.path.insert(0, matcha)

    # 必须在 import cosyvoice 之前打好补丁：
    # frontend.py 在模块导入/实例化时就会构造 Normalizer。
    _patch_wetext_offline()

    from cosyvoice.cli.cosyvoice import CosyVoice3

    runtime.torch_version = torch.__version__
    if torch.cuda.is_available():
        cap = torch.cuda.get_device_capability(0)
        archs = torch.cuda.get_arch_list()
        runtime.gpu_name = torch.cuda.get_device_name(0)
        runtime.arch_match = f"sm_{cap[0]}{cap[1]}" in archs
        runtime.device = "cuda"
    else:
        runtime.device = "cpu"

    t0 = time.time()
    # fp16 实测无效（慢 7.1%、显存 +27%），固定用 fp32
    model = CosyVoice3(str(MODEL_DIR), fp16=False)
    runtime.model = model
    runtime.sample_rate = model.sample_rate
    runtime.load_s = time.time() - t0
    logger.info("模型加载完成 %.1fs, 采样率 %d", runtime.load_s, runtime.sample_rate)

    # 缓存所有参考音频编码：实测省 16.5% 合成耗时。
    # 逐个注册而不是只注册一个，切换音色时才不用重新编码。
    t0 = time.time()
    runtime.voices = discover_voices()
    if not runtime.voices:
        raise RuntimeError(
            f"没有可用音色。内置参考音频缺失，且 {VOICES_DIR} 下无有效音色目录。"
        )
    for voice_id, prof in runtime.voices.items():
        try:
            model.add_zero_shot_spk(prof.ref_text, str(prof.ref_wav), voice_id)
            logger.info("音色已注册: %s", voice_id)
        except Exception:  # noqa: BLE001
            logger.exception("音色 %s 注册失败，已跳过", voice_id)
            runtime.voices.pop(voice_id, None)
    runtime.prompt_cached = bool(runtime.voices)
    logger.info("参考音频编码已缓存 %.2fs，可用音色 %s",
                time.time() - t0, list(runtime.voices))

    # 预热：避免首句承担 CUDA kernel 初始化开销
    t0 = time.time()
    warmup_voice = DEFAULT_VOICE_ID if DEFAULT_VOICE_ID in runtime.voices else next(iter(runtime.voices))
    warmup_prof = runtime.voices[warmup_voice]
    for _ in range(2):
        list(model.inference_zero_shot(
            "你好。", "", str(warmup_prof.ref_wav),
            zero_shot_spk_id=warmup_voice, stream=False))
    runtime.warmup_s = time.time() - t0
    logger.info("预热完成 %.2fs", runtime.warmup_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_model()
    yield
    runtime.model = None


app = FastAPI(title="CosyVoice3 TTS", lifespan=lifespan)


class SynthRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    voice_id: str = DEFAULT_VOICE_ID
    speed: float = Field(1.0, ge=0.5, le=2.0)


def _to_wav_bytes(speech: torch.Tensor, sample_rate: int) -> bytes:
    """把 tensor 波形编码成 16-bit PCM 单声道 WAV。"""
    data = speech.detach().cpu().float().numpy()
    if data.ndim > 1:
        data = data.squeeze()
    data = np.clip(data, -1.0, 1.0)
    ints = np.round(data * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(ints.tobytes())
    return buf.getvalue()


@app.get("/health")
async def health() -> JSONResponse:
    model = runtime.model
    if model is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    return JSONResponse({
        "ok": True,
        "engine": "cosyvoice3",
        "model": MODEL_DIR.name,
        "sample_rate": runtime.sample_rate,
        "device": runtime.device,
        "gpu": runtime.gpu_name,
        "arch_match": runtime.arch_match,
        "torch": runtime.torch_version,
        "load_s": round(runtime.load_s, 2),
        "warmup_s": round(runtime.warmup_s, 2),
        "prompt_cached": runtime.prompt_cached,
        "voice_id": DEFAULT_VOICE_ID,
        "voices": [p.to_dict() for p in runtime.voices.values()],
    })


@app.post("/synthesize")
async def synthesize(req: SynthRequest) -> Response:
    model = runtime.model
    if model is None:
        raise HTTPException(status_code=503, detail="model not loaded")

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")

    voice = req.voice_id or DEFAULT_VOICE_ID
    if voice not in runtime.voices:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown voice_id {voice!r}, available: "
                f"{sorted(runtime.voices)}"
            ),
        )
    profile = runtime.voices[voice]

    # 修复上游 bug：token_hop_len 是实例属性，出包后翻倍且不重置，
    # 会导致后续合成的首包越来越慢（实测 1.75s -> 2.25s）。
    # 非流式路径不读它，但为将来切流式保持一致，这里统一重置。
    if hasattr(model.model, "token_hop_len"):
        model.model.token_hop_len = 25

    started = time.perf_counter()
    try:
        chunks = list(model.inference_zero_shot(
            text, "", str(profile.ref_wav),
            zero_shot_spk_id=voice,
            stream=False,
            speed=req.speed,
        ))
    except Exception as exc:  # noqa: BLE001
        logger.exception("合成失败")
        raise HTTPException(
            status_code=500, detail=f"{type(exc).__name__}: {exc}"
        ) from exc

    total_ms = (time.perf_counter() - started) * 1000
    if not chunks:
        raise HTTPException(status_code=500, detail="model returned no audio")

    speech = chunks[0]["tts_speech"]
    duration = speech.shape[-1] / runtime.sample_rate
    wav = _to_wav_bytes(speech, runtime.sample_rate)
    rtf = (total_ms / 1000) / duration if duration > 0 else 0.0

    logger.info(
        "synthesize ok voice=%s chars=%d dur=%.2fs total=%.0fms rtf=%.3f",
        voice, len(text), duration, total_ms, rtf,
    )

    return Response(
        content=wav,
        media_type="audio/wav",
        headers={
            "x-sample-rate": str(runtime.sample_rate),
            "x-duration": f"{duration:.3f}",
            "x-total-ms": f"{total_ms:.1f}",
            "x-rtf": f"{rtf:.4f}",
            "x-engine": "cosyvoice3",
            "x-voice-id": voice,
        },
    )


@app.post("/synthesize_stream")
async def synthesize_stream(req: SynthRequest):
    """流式合成：边合成边把 PCM 分块推给调用方。

    为什么要这个接口（2026-11-12）：
    非流式的 ``/synthesize`` 必须等**整段**音频合成完才返回。实测 8 秒音频
    要 4.8 秒合成，这段时间板子完全静默，是端到端延迟的主要来源。

    CosyVoice 的 ``inference_zero_shot(stream=True)`` 会按句切分逐段 yield，
    首段通常 1~1.5 秒就能出来。调用方拿到首段即可立刻下发播放，
    后续段在播放期间继续合成，从而把「首字延迟」与「总合成时间」解耦。

    传输格式（简单、无歧义，不依赖 chunked 语义）：
        每块 = 4 字节大端 uint32 长度 + 该长度的裸 PCM16LE 单声道数据
        末尾发送一个长度=0 的块表示结束
    采样率通过响应头 ``x-sample-rate`` 告知。

    失败处理：若首块之前就出错，返回 500；若中途出错，直接断开连接
    （调用方会看到不完整流并报错），不发送伪造的结束块，避免静默截断。
    """
    model = runtime.model
    if model is None:
        raise HTTPException(status_code=503, detail="model not loaded")

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is empty")

    voice = req.voice_id or DEFAULT_VOICE_ID
    if voice not in runtime.voices:
        raise HTTPException(
            status_code=400,
            detail=f"unknown voice_id {voice!r}, available: {sorted(runtime.voices)}",
        )
    profile = runtime.voices[voice]

    # 与非流式路径一致：重置上游的 token_hop_len 污染
    if hasattr(model.model, "token_hop_len"):
        model.model.token_hop_len = 25
    # 关键：限制包大小上限，避免大包造成播放中断。
    #
    # 2026-11-23 修复「板子播放会卡一下」：
    #   上游 token_hop_len 从 25 按 ×2 增长到 token_max_hop_len（默认 100）。
    #   包越大，单次推理越久 → 分块到达间隔越大。
    #   实测 max_hop=100 时到达间隔达 2.85s，而板子当时只有 2.18s 音频，
    #   缓冲必然耗尽 → 播放中断（实测 playback 10.83s vs 音频 8.64s，多 2.2s）。
    #
    #   降到 50 后：分块均匀（1.16/2.0/2.0/2.0），最大间隔 2.85s→1.69s，
    #   饥饿时长 1.60s→0.47s（改善 71%），RTF 仍 <1（0.94）。
    #
    #   不要降到 25：实测 RTF 升到 1.13（合成跟不上播放），饥饿反而恶化。
    if hasattr(model.model, "token_max_hop_len"):
        model.model.token_max_hop_len = 50

    sample_rate = runtime.sample_rate

    async def gen():
        started = time.perf_counter()
        first_ms: float | None = None
        total_samples = 0
        try:
            # inference_* 是同步生成器，放到线程里逐块取，避免阻塞事件循环
            it = model.inference_zero_shot(
                text, "", str(profile.ref_wav),
                zero_shot_spk_id=voice,
                stream=True,
                speed=req.speed,
            )
            loop = asyncio.get_running_loop()
            while True:
                chunk = await loop.run_in_executor(None, _next_or_none, it)
                if chunk is None:
                    break
                speech = chunk["tts_speech"]
                # [1, N] float32 -> int16 LE 字节
                pcm = _to_pcm16_bytes(speech)
                if not pcm:
                    continue
                if first_ms is None:
                    first_ms = (time.perf_counter() - started) * 1000
                total_samples += len(pcm) // 2
                # 按协议上限切块下发。
                #
                # 为什么必须切（2026-11-23 修复）：
                #   inference_zero_shot(stream=True) 按「句」yield，一整句 PCM
                #   可能远超协议单块上限（实测 130560 / 195840 字节）。
                #   zjy13 的 TTSClient 会校验长度字段，超限直接抛
                #   「TTS 流式分块长度异常」——表现为板子播到一半中止，
                #   且因 TTS 失败导致播放状态没建立，后续块被拒。
                #   切块后每块 ≤ 48KB，与 MAX_AUDIO_CHUNK_BYTES 一致。
                for offset in range(0, len(pcm), MAX_STREAM_CHUNK_BYTES):
                    piece = pcm[offset:offset + MAX_STREAM_CHUNK_BYTES]
                    yield struct.pack(">I", len(piece)) + piece
            # 结束标记
            yield struct.pack(">I", 0)
            duration = total_samples / sample_rate if sample_rate else 0.0
            logger.info(
                "stream ok voice=%s chars=%d dur=%.2fs first=%.0fms total=%.0fms",
                voice, len(text), duration,
                first_ms if first_ms is not None else -1,
                (time.perf_counter() - started) * 1000,
            )
        except Exception:  # noqa: BLE001
            logger.exception("流式合成失败（已发送部分数据）")
            # 不发结束标记：让调用方明确知道这是不完整的
            raise

    return StreamingResponse(
        gen(),
        media_type="application/octet-stream",
        headers={
            "x-sample-rate": str(sample_rate),
            "x-engine": "cosyvoice3",
            "x-voice-id": voice,
        },
    )


def _next_or_none(it):
    """从同步生成器取下一个元素，耗尽时返回 None（供 run_in_executor 使用）。"""
    try:
        return next(it)
    except StopIteration:
        return None


def _to_pcm16_bytes(speech) -> bytes:
    """把 [1, N] 的 float32 tensor 转成 int16 小端裸 PCM。"""
    arr = speech.detach().to("cpu", dtype=torch.float32).numpy()
    if arr.ndim > 1:
        arr = arr.reshape(-1)
    clipped = np.clip(arr, -1.0, 1.0)
    return np.round(clipped * 32767.0).astype("<i2").tobytes()


def main() -> None:
    import uvicorn

    port = int(os.environ.get("TTS_PORT", "50000"))
    # 只监听回环（V2 第六节第 10 条）
    host = os.environ.get("TTS_HOST", "127.0.0.1")
    logger.info("启动 TTS 服务 http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
