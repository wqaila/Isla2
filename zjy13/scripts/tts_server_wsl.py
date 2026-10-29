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

import io
import logging
import os
import sys
import time
import wave
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

COSYVOICE_DIR = Path.home() / "CosyVoice"
MODEL_DIR = Path.home() / "models" / "Fun-CosyVoice3-0.5B-2512"
PROMPT_WAV = COSYVOICE_DIR / "asset" / "zero_shot_prompt.wav"
PROMPT_TEXT = (
    "You are a helpful assistant.<|endofprompt|>希望你以后能够做的比我还好呦。"
)
SPK_ID = "xiaoman"

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

    # 内置音色：CosyVoice 自带的示例参考音频
    if PROMPT_WAV.is_file():
        voices[SPK_ID] = VoiceProfile(
            voice_id=SPK_ID,
            ref_wav=PROMPT_WAV,
            ref_text=PROMPT_TEXT,
            is_builtin=True,
        )

    if not VOICES_DIR.is_dir():
        logger.warning("音色目录不存在，仅内置音色可用: %s", VOICES_DIR)
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
    warmup_voice = SPK_ID if SPK_ID in runtime.voices else next(iter(runtime.voices))
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
    voice_id: str = SPK_ID
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
        "voice_id": SPK_ID,
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

    voice = req.voice_id or SPK_ID
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


def main() -> None:
    import uvicorn

    port = int(os.environ.get("TTS_PORT", "50000"))
    # 只监听回环（V2 第六节第 10 条）
    host = os.environ.get("TTS_HOST", "127.0.0.1")
    logger.info("启动 TTS 服务 http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
