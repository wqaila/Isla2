"""FunASR 引擎封装。

设计要点（对应方案文档第三节）：
- 模型**只加载一次**，后续请求复用，不重复加载
- 首次加载后执行**预热**，避免第一句请求承担初始化开销
- 启动时检查 CUDA 可用性，并提供 CPU 回退或清晰错误
- 记录音频时长、推理耗时、识别结果与设备信息
- 模型缓存路径可配置

关于 sm_120 的重要说明（实测教训）：
``torch.cuda.is_available()`` 在架构不匹配时**仍可能返回 True**。
必须额外校验 ``torch.cuda.get_arch_list()`` 是否包含当前设备的算力架构，
否则会静默回退到 PTX JIT，表现为"能跑但极慢"。
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .audio_utils import (
    AudioFormatError,
    NormalizeResult,
    normalize_to_asr_wav,
    probe_wav,
    read_pcm,
)

logger = logging.getLogger("asr.engine")

DEFAULT_ASR_MODEL = "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
DEFAULT_VAD_MODEL = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
DEFAULT_PUNC_MODEL = "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch"


class ASRError(RuntimeError):
    """ASR 运行错误。"""


class CUDANotAvailableError(ASRError):
    """CUDA 不可用或架构不匹配。"""


@dataclass(slots=True)
class DeviceInfo:
    """设备与运行环境快照，用于日志与验收报告。"""

    torch_version: str
    cuda_version: str | None
    cuda_available: bool
    device_name: str | None
    capability: tuple[int, int] | None
    arch_list: list[str]
    arch_match: bool | None
    funasr_version: str
    using_gpu: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "torch_version": self.torch_version,
            "cuda_version": self.cuda_version,
            "cuda_available": self.cuda_available,
            "device_name": self.device_name,
            "capability": list(self.capability) if self.capability else None,
            "arch_list": self.arch_list,
            "arch_match": self.arch_match,
            "funasr_version": self.funasr_version,
            "using_gpu": self.using_gpu,
        }

    def summary(self) -> str:
        if not self.cuda_available:
            return f"CPU 模式（torch {self.torch_version}，CUDA 不可用）"
        return (
            f"{self.device_name} sm_{self.capability[0]}{self.capability[1]} "
            f"| torch {self.torch_version} / CUDA {self.cuda_version} "
            f"| 架构匹配={self.arch_match}"
        )


@dataclass(slots=True)
class TranscribeResult:
    """一次识别的完整记录（文档第十五节计时点）。"""

    text: str
    audio_duration_s: float
    infer_ms: float
    total_ms: float
    realtime_factor: float
    device: DeviceInfo
    normalize: NormalizeResult | None = None
    model_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "audio_duration_s": round(self.audio_duration_s, 3),
            "infer_ms": round(self.infer_ms, 1),
            "total_ms": round(self.total_ms, 1),
            "realtime_factor": round(self.realtime_factor, 4),
            "model_id": self.model_id,
            "device": self.device.to_dict(),
            "normalize": self.normalize.to_dict() if self.normalize else None,
            **self.extra,
        }


def _funasr_version() -> str:
    try:
        import funasr

        return getattr(funasr, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return "not-installed"


def check_device(*, require_gpu: bool = True) -> DeviceInfo:
    """检查 torch / CUDA 环境。

    除了 ``is_available()``，还校验编译时的架构列表是否覆盖当前设备算力，
    避免"看起来可用实际回退"的陷阱。
    """
    try:
        import torch
    except ImportError as exc:
        raise ASRError("未安装 PyTorch，请先安装 torch（CUDA 12.8 构建）") from exc

    torch_version = torch.__version__
    cuda_version = getattr(torch.version, "cuda", None)
    available = bool(torch.cuda.is_available())

    device_name = None
    capability = None
    arch_list: list[str] = []
    arch_match: bool | None = None

    if available:
        try:
            device_name = torch.cuda.get_device_name(0)
            capability = torch.cuda.get_device_capability(0)
            arch_list = list(torch.cuda.get_arch_list())
            if capability:
                target = f"sm_{capability[0]}{capability[1]}"
                # arch_list 里也可能出现 compute_XX 形式
                arch_match = target in arch_list or any(
                    a.startswith(f"compute_{capability[0]}{capability[1]}") for a in arch_list
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取 GPU 信息失败: %s", exc)

    info = DeviceInfo(
        torch_version=torch_version,
        cuda_version=cuda_version,
        cuda_available=available,
        device_name=device_name,
        capability=capability,
        arch_list=arch_list,
        arch_match=arch_match,
        funasr_version=_funasr_version(),
        using_gpu=available and (arch_match is not False),
    )

    if require_gpu:
        if not available:
            raise CUDANotAvailableError(
                "CUDA 不可用。若需要 CPU 回退，请传 require_gpu=False。"
                f" 当前 torch={torch_version}, CUDA={cuda_version}"
            )
        if arch_match is False:
            raise CUDANotAvailableError(
                f"PyTorch 编译的架构列表不包含 {device_name} 的 "
                f"sm_{capability[0]}{capability[1]}。\n"
                f"当前支持的架构: {', '.join(arch_list)}\n"
                "这会导致算子缺失或静默回退到 PTX JIT（极慢）。\n"
                "请安装 CUDA 12.8+ 构建的 PyTorch（>=2.7.0），"
                "例如 torch==2.8.0+cu128。"
            )
    return info


class ASREngine:
    """Paraformer 识别引擎。模型加载一次，多次复用。"""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_ASR_MODEL,
        cache_dir: str | Path | None = None,
        device: str = "cuda:0",
        require_gpu: bool = True,
        use_vad: bool = False,
        use_punc: bool = False,
        vad_model_id: str = DEFAULT_VAD_MODEL,
        punc_model_id: str = DEFAULT_PUNC_MODEL,
        warmup: bool = True,
    ) -> None:
        self.model_id = model_id
        # 默认放在项目内的 _models，避免写用户目录（受限环境会拒绝访问）
        default_cache = Path(__file__).resolve().parent.parent / "_models"
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache
        self.device = device
        self.require_gpu = require_gpu
        self.use_vad = use_vad
        self.use_punc = use_punc
        self.vad_model_id = vad_model_id
        self.punc_model_id = punc_model_id
        self.warmup = warmup

        self._model: Any = None
        self._device_info: DeviceInfo | None = None
        self._load_ms: float = 0.0
        self._warmup_ms: float | None = None

    # ------------------------------------------------------------------

    @property
    def device_info(self) -> DeviceInfo:
        if self._device_info is None:
            raise ASRError("引擎尚未加载，请先调用 load()")
        return self._device_info

    @property
    def load_ms(self) -> float:
        return self._load_ms

    @property
    def warmup_ms(self) -> float | None:
        return self._warmup_ms

    @property
    def loaded(self) -> bool:
        return self._model is not None

    # ------------------------------------------------------------------

    def load(self) -> DeviceInfo:
        """加载模型（幂等）。"""
        if self._model is not None:
            return self.device_info

        self._device_info = check_device(require_gpu=self.require_gpu)

        # 必须在 import funasr 之前设置，且不能用 setdefault：
        # ModelScope 会在导入时读取并缓存路径，默认写 C:\Users\<user>\.modelscope。
        # 若该目录不可写（受限环境/沙箱），会抛 [WinError 5] 拒绝访问。
        cache = str(self.cache_dir)
        os.environ["MODELSCOPE_CACHE"] = cache
        os.environ["MODELSCOPE_HOME"] = cache
        os.environ.setdefault("HF_HOME", str(self.cache_dir / "hf"))
        os.environ.setdefault("TRANSFORMERS_CACHE", str(self.cache_dir / "hf"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise ASRError(
                "未安装 FunASR。请执行: pip install funasr"
            ) from exc

        # 优先使用本地目录，避免触发 ModelScope 的在线下载逻辑。
        # 原因（实测）：FunASR 的 get_or_download_model_dir 调用 snapshot_download 时
        # 没有传 cache_dir，会用默认的 ~/.cache/modelscope（旧版 ~/.modelscope），
        # 在受限环境会抛 [WinError 5] 拒绝访问。
        # 只要 model 参数是一个已存在的目录，FunASR 就不会走下载分支。
        model_arg: str = self.model_id
        local_dir = self.cache_dir / self.model_id.split("/")[-1]
        if local_dir.is_dir() and any(local_dir.iterdir()):
            model_arg = str(local_dir)
            logger.info("使用本地模型目录: %s", model_arg)

        kwargs: dict[str, Any] = {
            "model": model_arg,
            "device": self.device if self._device_info.using_gpu else "cpu",
            "disable_update": True,
        }
        if self.use_vad:
            kwargs["vad_model"] = self._resolve_aux(self.vad_model_id)
        if self.use_punc:
            kwargs["punc_model"] = self._resolve_aux(self.punc_model_id)

        logger.info("加载 ASR 模型 %s (device=%s)", model_arg, kwargs["device"])
        t0 = time.perf_counter()
        try:
            self._model = AutoModel(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ASRError(f"模型加载失败: {type(exc).__name__}: {exc}") from exc
        self._load_ms = (time.perf_counter() - t0) * 1000
        logger.info("模型加载完成，耗时 %.0f ms", self._load_ms)

        if self.warmup:
            self._do_warmup()
        return self.device_info

    def _resolve_aux(self, model_id: str) -> str:
        """辅助模型（VAD/标点）同样优先用本地目录。"""
        local = self.cache_dir / model_id.split("/")[-1]
        if local.is_dir() and any(local.iterdir()):
            return str(local)
        return model_id

    def _do_warmup(self) -> None:
        """用一段极短音频预热，避免首个真实请求承担初始化开销。

        同样传 ndarray 而不是文件路径，理由见 transcribe_file 里的注释。
        """
        try:
            # 用极短的随机噪声而非纯静音：部分实现会对全零输入短路，
            # 达不到"预热"效果。
            rng = np.random.default_rng(0)
            probe = (rng.standard_normal(int(0.3 * 16000)) * 0.01).astype(np.float32)
            t0 = time.perf_counter()
            self._model.generate(input=probe, batch_size_s=0, fs=16000)
            self._warmup_ms = (time.perf_counter() - t0) * 1000
            logger.info("预热完成，耗时 %.0f ms", self._warmup_ms)
        except Exception as exc:  # noqa: BLE001
            logger.warning("预热失败（不影响后续识别）: %s", exc)
            self._warmup_ms = None

    # ------------------------------------------------------------------

    def transcribe_file(
        self,
        audio_path: str | Path,
        *,
        work_dir: str | Path | None = None,
        normalize: bool = True,
    ) -> TranscribeResult:
        """识别一个音频文件。

        :param normalize: 是否先规范化为 16k/单声道/16bit。
            无论是否规范化，都会记录源格式。
        """
        if self._model is None:
            self.load()

        src = Path(audio_path)
        if not src.is_file():
            raise ASRError(f"音频文件不存在: {src}")

        work = Path(work_dir) if work_dir else self.cache_dir / "_work"
        work.mkdir(parents=True, exist_ok=True)

        started = time.perf_counter()

        norm: NormalizeResult | None = None
        target = src
        if normalize:
            try:
                norm = normalize_to_asr_wav(src, work)
            except AudioFormatError as exc:
                raise ASRError(str(exc)) from exc
            target = norm.wav_path
            logger.info("%s", norm.summary())
        else:
            info = probe_wav(src)
            if info.sample_rate != 16000 or info.channels != 1:
                logger.warning(
                    "未规范化但格式非 16k/单声道：%s，识别质量可能受影响", info.describe()
                )

        duration = probe_wav(target).duration_s

        t0 = time.perf_counter()
        try:
            # 用 numpy 数组而非文件路径传入。
            # 原因（实测）：FunASR 读文件依赖 soundfile(libsndfile.dll) 或 ffmpeg，
            # 本机两者都缺，传路径会报
            #   "Couldn't find appropriate backend to handle uri ..."
            # 而 generate 接受 ndarray（float32，[-1,1]），可绕开这两个外部依赖。
            data, _sr = read_pcm(target)
            outputs = self._model.generate(
                input=data.astype(np.float32), batch_size_s=0, fs=_sr
            )
        except Exception as exc:  # noqa: BLE001
            raise ASRError(f"识别失败: {type(exc).__name__}: {exc}") from exc
        infer_ms = (time.perf_counter() - t0) * 1000

        text = ""
        if isinstance(outputs, list) and outputs:
            first = outputs[0]
            if isinstance(first, dict):
                text = str(first.get("text", "")).strip()
            elif isinstance(first, str):
                text = first.strip()

        total_ms = (time.perf_counter() - started) * 1000
        rtf = (infer_ms / 1000) / duration if duration > 0 else 0.0

        return TranscribeResult(
            text=text,
            audio_duration_s=duration,
            infer_ms=infer_ms,
            total_ms=total_ms,
            realtime_factor=rtf,
            device=self.device_info,
            normalize=norm,
            model_id=self.model_id,
        )


__all__ = [
    "ASREngine",
    "ASRError",
    "CUDANotAvailableError",
    "DeviceInfo",
    "TranscribeResult",
    "check_device",
    "DEFAULT_ASR_MODEL",
    "DEFAULT_VAD_MODEL",
    "DEFAULT_PUNC_MODEL",
]
