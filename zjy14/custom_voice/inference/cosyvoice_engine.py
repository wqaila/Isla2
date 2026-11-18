"""CosyVoice3 真引擎实现。

实现 ``TTSEngine`` 协议，把上游 CosyVoice3 包装成项目统一接口。

关键设计（每一条都有实测依据，见 docs/ 下报告）：

1. **token_hop_len 每请求重置**
   上游该属性跨调用翻倍且不重置，导致首包延迟 1.75s → 2.25s。

2. **参考音频编码缓存**
   用 ``add_zero_shot_spk`` 注册音色，实测省 16.5% 合成耗时。

3. **文本长度比检查**
   合成文本远短于参考文本时音色会漂移（实测质心 +490Hz vs -56Hz）。
   上游 cosyvoice.py:94 也以 0.5 为警告阈值，本引擎记录并暴露该指标。

4. **文本前端本地加载**
   wetext 的 __init__ 无条件联网校验，无 token 时降级。
   用 ``enable_wetext_frontend`` 读本地 fst 绕过。

5. **fp32 固定**
   实测 fp16 更慢 7.1%、显存 +27%。

6. **参考音频自动裁首尾静音**
   候选素材含 22-34% 纯数字零静音（噪声门痕迹），
   裁掉后 HNR 提升 2-5.5dB、平坦度下降 0.09-0.16。
"""

from __future__ import annotations

import io
import time
import wave
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import numpy as np

from ..config.schema import GPUConfig, Precision, TextFrontendMode
from ..core.errors import (
    EngineNotReadyError,
    InvalidRequestError,
    SynthesisError,
)
from ..core.logging import get_logger
from ..expression.control import PAUSE_PROFILES
from ..schemas.synthesis import (
    AudioChunk,
    EngineStats,
    SynthesisRequest,
    SynthesisResult,
)

if TYPE_CHECKING:
    from ..expression.control import ExpressionParams

logger = get_logger("engine.cosyvoice")

#: CosyVoice3 原生采样率
SAMPLE_RATE = 24000

#: 参考文本必需的前缀分隔符。
#: 缺失时上游会在卷积层报 "Kernel size can't be greater than actual input size"。
PROMPT_PREFIX = "You are a helpful assistant.<|endofprompt|>"

#: 默认模型目录
DEFAULT_MODEL_DIR = Path.home() / "models" / "Fun-CosyVoice3-0.5B-2512"

#: 中文正常语速（字/秒）。用于校验参考文本与音频是否匹配。
#: 依据：官方参考 4.02、当前参考 4.33，实测素材 4.17-4.60。
NORMAL_CHARS_PER_SEC = 4.2

#: 参考文本字数 / (音频时长 × 正常语速) 的合理区间。
#: 超出说明文本与音频严重不匹配 —— 上游会因此错位，
#: 表现为合成时报 "Kernel size can't be greater than actual input size"。
#: 实测案例：文本只标了 11 字但音频 4.5 秒（应有 19 字），比值 0.58。
REF_TEXT_RATIO_MIN = 0.6
REF_TEXT_RATIO_MAX = 1.6


def _block_modelscope_download() -> Any:
    """临时禁止 modelscope 联网下载，返回用于恢复的原始函数。

    为什么需要（实测教训）：
      CosyVoice3.__init__ -> CosyVoiceFrontEnd.__init__ 会 `import wetext`，
      而 wetext 的 __init__ 无条件调 snapshot_download()。
      无 token 时它会先尝试下载部分文件，实测卡住 **90 秒以上**才失败，
      把服务启动时间从 8.5s 拖到 100s+。

      我们的 enable_wetext_frontend 补丁在构造之后才生效，救不了这次下载。
      所以在构造前把 snapshot_download 换成抛异常，让它快速走 except 分支。

    :return: 原始的 snapshot_download（供 finally 恢复）；不可用时返回 None
    """
    try:
        import modelscope
    except ImportError:
        return None

    original = getattr(modelscope, "snapshot_download", None)
    if original is None:
        return None

    def _blocked(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("zjy14: 启动期禁止 modelscope 联网下载（使用本地缓存）")

    modelscope.snapshot_download = _blocked
    return original


def _restore_modelscope_download(original: Any) -> None:
    if original is None:
        return
    try:
        import modelscope

        modelscope.snapshot_download = original
    except ImportError:
        pass


def check_reference_consistency(audio_path: str, prompt_text: str) -> tuple[bool, str]:
    """校验参考文本与音频时长是否匹配。

    上游对参考文本与音频的对齐要求很严格：
    文本明显少于音频实际内容时，LLM 前缀错位，生成阶段会拿到过短
    token 序列，在卷积层报 "Kernel size can't be greater than actual input size"。

    这个检查把该错误提前到启动期，并给出可操作的修复建议。

    :return: (是否通过, 说明)
    """
    try:
        import soundfile as sf
    except ImportError:
        return True, "soundfile 不可用，跳过校验"

    try:
        info = sf.info(audio_path)
        duration = float(info.duration)
    except Exception as exc:  # noqa: BLE001
        return True, f"无法读取音频信息，跳过校验（{type(exc).__name__}）"

    if duration <= 0:
        return True, "音频时长为 0，跳过校验"

    # 去掉前缀与标点后统计实际字数
    body = prompt_text
    if "<|endofprompt|>" in body:
        body = body.split("<|endofprompt|>", 1)[1]
    n_chars = len([c for c in body if c not in "，。！？、；：,.!?;: \n\t"])

    if n_chars == 0:
        return False, "参考文本为空（去掉前缀后无内容）"

    expected = duration * NORMAL_CHARS_PER_SEC
    ratio = n_chars / expected if expected > 0 else 0.0

    if ratio < REF_TEXT_RATIO_MIN:
        return False, (
            f"参考文本疑似不完整：音频 {duration:.2f}s 按正常语速"
            f"（{NORMAL_CHARS_PER_SEC} 字/秒）应含约 {expected:.0f} 字，"
            f"但只标注了 {n_chars} 字（比值 {ratio:.2f}）。"
            "文本与音频不对齐会导致合成时报 "
            "'Kernel size can't be greater than actual input size'。"
            "请补全参考文本，或换一段文本已知的音频。"
        )
    if ratio > REF_TEXT_RATIO_MAX:
        return False, (
            f"参考文本疑似过多：音频 {duration:.2f}s 应含约 {expected:.0f} 字，"
            f"但标注了 {n_chars} 字（比值 {ratio:.2f}）。"
            "请核对参考文本是否与音频逐字对应。"
        )
    return True, f"参考文本与音频匹配（{n_chars} 字 / {duration:.2f}s，比值 {ratio:.2f}）"


def _to_pcm16(speech: Any) -> bytes:
    """torch tensor -> PCM S16LE。"""
    data = speech.detach().cpu().float().numpy()
    if data.ndim > 1:
        data = data.squeeze()
    data = np.clip(data, -1.0, 1.0)
    return np.round(data * 32767.0).astype("<i2").tobytes()


def _to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


class CosyVoiceEngine:
    """CosyVoice3 引擎。实现 ``TTSEngine`` 协议。"""

    kind = "cosyvoice3"

    def __init__(self, config: GPUConfig) -> None:
        self._config = config
        self._model: Any | None = None
        self._ready = False
        self._load_s = 0.0
        self._warmup_s = 0.0
        self._voices: dict[str, dict[str, Any]] = {}
        self._text_frontend = ""
        self._peak_vram_mb = 0.0
        self._model_dir = Path(config.model_dir) if config.model_dir else DEFAULT_MODEL_DIR
        self._ratio_warnings = 0
        self._hop_resets = 0
        self._compile_applied = False
        # 归一化为枚举：model_copy(update=...) 不做类型校验，
        # 直接传字符串时 `is` 比较会失效，导致配置项静默不生效。
        self._text_frontend_mode = TextFrontendMode(config.text_frontend)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _apply_compile(self) -> None:
        """按配置套用 torch.compile。

        实测收益（2026-11-16，8 轮交错采样排除顺序效应）：
          基线   中位 3.049s  均值 2.986s
          compile 中位 2.783s  均值 2.808s
          配对差值均值 -0.177s（相对改善 8.7%），8 轮中 7 轮更快
          噪声参考（基线标准差/2）0.084s，收益是其 2 倍以上

        只编译 LLM 子模块：它是自回归瓶颈（占首包约 52%）。
        不编译 flow/hift：结构更复杂，graph break 风险高，收益不明确。

        代价：首次推理触发编译，约多花 10-60s（实测本机约 1.2s 套用
        + 首次推理额外开销）。因此只适合「常驻服务」场景，
        冷启动敏感时建议关闭。
        """
        if not self._config.enable_compile:
            return

        try:
            import torch
        except ImportError:
            logger.warning("torch 不可用，跳过 compile")
            return

        # 目标：self._model.model.llm（CosyVoice3Model 的 LLM 子模块）
        inner = getattr(self._model, "model", None)
        llm = getattr(inner, "llm", None) if inner is not None else None
        if llm is None:
            logger.warning(
                "找不到 LLM 子模块，跳过 compile",
                extra={
                    "extra_fields": {
                        "has_inner_model": inner is not None,
                        "model_type": type(self._model).__name__,
                    }
                },
            )
            return

        try:
            t0 = time.perf_counter()
            inner.llm = torch.compile(llm, dynamic=False)
            elapsed = time.perf_counter() - t0
            self._compile_applied = True
            logger.info(
                "torch.compile 已套用到 LLM（首次推理会触发编译）",
                extra={"extra_fields": {"apply_s": round(elapsed, 2)}},
            )
        except Exception:  # noqa: BLE001
            # compile 失败不应阻断启动，降级为未编译
            logger.exception("torch.compile 套用失败，降级为未编译")
            self._compile_applied = False

    def _reset_hop_len(self) -> None:
        """重置上游 token_hop_len 污染。

        上游 cosyvoice/cli/model.py 里该属性每出一包就翻倍
        （25 → 50 → 100）且合成结束后不重置，导致后续请求首包越来越慢。
        """
        if not self._config.token_hop_len_reset or self._model is None:
            return
        model = getattr(self._model, "model", None)
        if model is not None and hasattr(model, "token_hop_len"):
            model.token_hop_len = 25
            self._hop_resets += 1

    def _setup_text_frontend(self) -> None:
        """按配置启用文本前端。"""
        mode = self._text_frontend_mode
        if mode is TextFrontendMode.NONE:
            self._text_frontend = ""
            logger.info("文本前端按配置禁用")
            return

        from .text_frontend import TextFrontendError, enable_wetext_frontend

        try:
            enable_wetext_frontend(self._model.frontend)
            self._text_frontend = getattr(self._model.frontend, "text_frontend", "")
            logger.info(
                "文本前端已启用",
                extra={"extra_fields": {"text_frontend": self._text_frontend}},
            )
        except TextFrontendError as exc:
            if mode is TextFrontendMode.WETEXT:
                # 强制模式：失败必须报错，不静默降级
                raise
            self._text_frontend = ""
            logger.warning(
                "文本前端启用失败，已降级为无前端（数字/符号读法可能不准）",
                extra={"extra_fields": {"reason": str(exc)[:200]}},
            )

    def _check_text_ratio(self, text: str, prompt_text: str) -> float:
        """检查合成文本与参考文本的长度比。

        实测：比值 0.21 时音色质心偏移 +490Hz，1.08 时仅 -56Hz。
        返回实际比值，供日志与指标使用。
        """
        if not prompt_text:
            return 1.0
        ratio = len(text) / len(prompt_text)
        if ratio < self._config.min_text_ratio:
            self._ratio_warnings += 1
            logger.warning(
                "合成文本过短，音色可能漂移",
                extra={
                    "extra_fields": {
                        "text_len": len(text),
                        "prompt_len": len(prompt_text),
                        "ratio": round(ratio, 3),
                        "threshold": self._config.min_text_ratio,
                    }
                },
            )
        return ratio

    def _refresh_vram(self) -> None:
        from .gpu_info import gpu_info

        info = gpu_info()
        if info.get("ok"):
            peak = info.get("peak_allocated_mb")
            if isinstance(peak, (int, float)):
                self._peak_vram_mb = float(peak)

    # ------------------------------------------------------------------
    # TTSEngine 协议
    # ------------------------------------------------------------------

    def load(self) -> None:
        """加载模型。幂等。"""
        if self._ready:
            return

        if not self._model_dir.is_dir():
            raise EngineNotReadyError(
                f"模型目录不存在：{self._model_dir}。"
                "请检查 configs/gpu.yaml 的 model_dir，或确认模型已下载。"
            )

        t0 = time.perf_counter()

        # 上游要求把仓库根目录与 Matcha-TTS 加入 sys.path
        import sys

        repo = Path.home() / "CosyVoice"
        for p in (repo, repo / "third_party" / "Matcha-TTS"):
            if p.is_dir() and str(p) not in sys.path:
                sys.path.insert(0, str(p))

        try:
            from cosyvoice.cli.cosyvoice import CosyVoice3
        except ImportError as exc:
            raise EngineNotReadyError(
                "无法导入 cosyvoice。请确认：\n"
                "  1. 在 WSL 的 ~/cosyvoice-venv 环境中运行\n"
                "  2. ~/CosyVoice 仓库存在且依赖已安装\n"
                f"原始错误: {exc}"
            ) from exc

        # 实测 fp16 更慢且更耗显存，强制 fp32
        fp16 = self._config.precision is Precision.FP16
        if fp16:
            logger.warning(
                "配置要求 fp16，但实测本机 fp16 更慢 7.1%%、显存 +27%%，仍按配置执行",
                extra={"extra_fields": {"precision": "fp16"}},
            )

        # 关键：构造前禁止 modelscope 联网下载。
        # 否则 wetext 的 snapshot_download 会卡 90s+ 才失败（无 token 时）。
        _orig_download = _block_modelscope_download()
        try:
            self._model = CosyVoice3(str(self._model_dir), fp16=fp16)
        except Exception as exc:  # noqa: BLE001
            raise EngineNotReadyError(
                f"模型加载失败：{type(exc).__name__}: {exc}"
            ) from exc
        finally:
            _restore_modelscope_download(_orig_download)

        self._load_s = time.perf_counter() - t0
        self._setup_text_frontend()

        # 注册所有可用音色（缓存参考音频编码）
        t0 = time.perf_counter()
        self._register_voices()
        cache_s = time.perf_counter() - t0

        # torch.compile（若启用）。必须放在预热之前，
        # 让预热承担首次编译开销，避免首个真实请求被拖慢。
        self._apply_compile()

        # 预热：避免首句承担 CUDA kernel 初始化开销
        # （启用 compile 时还承担首次编译）
        t0 = time.perf_counter()
        self._warmup()
        self._warmup_s = time.perf_counter() - t0

        self._ready = True
        self._refresh_vram()

        logger.info(
            "CosyVoice3 引擎就绪",
            extra={
                "extra_fields": {
                    "model_dir": str(self._model_dir),
                    "load_s": round(self._load_s, 2),
                    "warmup_s": round(self._warmup_s, 2),
                    "voice_cache_s": round(cache_s, 2),
                    "voices": list(self._voices),
                    "text_frontend": self._text_frontend,
                    "sample_rate": SAMPLE_RATE,
                }
            },
        )

    def _register_voices(self) -> None:
        """从数据库读取声音档案并注册到模型。

        优先从 SQLite 读（Phase 3 的管理数据）；
        数据库不可用时回退到扫描 zjy13 的 voices 目录。
        """
        voices = self._load_voices_from_db()
        if not voices:
            voices = self._load_voices_from_dir()

        for voice_id, info in voices.items():
            # 先校验参考文本与音频是否匹配，避免合成时才在卷积层崩
            ok, msg = check_reference_consistency(
                info["audio_path"], info["prompt_text"]
            )
            if not ok:
                logger.error(
                    "参考文本校验未通过，跳过该音色",
                    extra={
                        "extra_fields": {"voice_id": voice_id, "reason": msg}
                    },
                )
                continue
            logger.info(
                "参考文本校验通过",
                extra={"extra_fields": {"voice_id": voice_id, "detail": msg}},
            )

            try:
                self._model.add_zero_shot_spk(
                    info["prompt_text"], info["audio_path"], voice_id
                )
                self._voices[voice_id] = info
                logger.info(
                    "音色已注册",
                    extra={"extra_fields": {"voice_id": voice_id}},
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "音色注册失败，已跳过",
                    extra={"extra_fields": {"voice_id": voice_id}},
                )

        if not self._voices:
            raise EngineNotReadyError(
                "没有可用的音色。请先通过 POST /api/v1/speakers 导入声音档案，"
                "或确认 zjy13/server/characters/voices/ 下有有效的参考音频与文本。"
            )

    def _load_voices_from_db(self) -> dict[str, dict[str, Any]]:
        """从 SQLite 的 speakers 表读取音色。"""
        try:
            from ..core.paths import db_path
            from ..db.session import Database

            if not db_path().is_file():
                return {}
            db = Database()
            try:
                conn = db.connect()
                rows = conn.execute(
                    "SELECT speaker_id, audio_path, transcript FROM speakers"
                ).fetchall()
                out: dict[str, dict[str, Any]] = {}
                for r in rows:
                    audio = Path(r["audio_path"])
                    text = (r["transcript"] or "").strip()
                    if not audio.is_file() or not text:
                        logger.warning(
                            "声音档案不完整，跳过",
                            extra={
                                "extra_fields": {
                                    "speaker_id": r["speaker_id"],
                                    "audio_exists": audio.is_file(),
                                    "has_transcript": bool(text),
                                }
                            },
                        )
                        continue
                    if "<|endofprompt|>" not in text:
                        text = PROMPT_PREFIX + text
                    out[r["speaker_id"]] = {
                        "audio_path": str(audio),
                        "prompt_text": text,
                    }
                return out
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            logger.exception("从数据库读取音色失败，将回退到目录扫描")
            return {}

    def _load_voices_from_dir(self) -> dict[str, dict[str, Any]]:
        """回退方案：扫描 zjy13 的 voices 目录。"""
        from ..core.paths import REFERENCE_AUDIO_DIR

        out: dict[str, dict[str, Any]] = {}
        if not REFERENCE_AUDIO_DIR.is_dir():
            return out

        for sub in sorted(REFERENCE_AUDIO_DIR.iterdir()):
            if not sub.is_dir():
                continue
            wavs = sorted(sub.glob("ref_*.wav")) or sorted(sub.glob("*.wav"))
            if not wavs:
                continue
            wav = wavs[0]
            txt = wav.with_suffix(".txt")
            if not txt.is_file():
                continue
            text = txt.read_text(encoding="utf-8").strip()
            if not text:
                continue
            if "<|endofprompt|>" not in text:
                text = PROMPT_PREFIX + text
            out[sub.name] = {"audio_path": str(wav), "prompt_text": text}
        return out

    def _warmup(self) -> None:
        """预热：跑两次短合成，让 CUDA kernel 就绪。

        注意：这里**不能**用 ``_pick_voice(default_voice_id)``。
        配置里的默认音色可能不存在于当前音色集合（如测试环境只有
        ``testvoice`` 而配置写 ``elysia``）。预热只需任意一个可用音色，
        用配置值会因「未知音色报错」而阻断启动。

        未指定音色时的兜底逻辑仍由 ``_pick_voice`` 负责，
        此处直接取第一个可用音色。
        """
        if not self._voices:
            logger.warning("没有可用音色，跳过预热")
            return

        voice = next(iter(self._voices))
        info = self._voices[voice]
        for _ in range(2):
            self._reset_hop_len()
            try:
                list(
                    self._model.inference_zero_shot(
                        "你好。",
                        "",
                        info["audio_path"],
                        zero_shot_spk_id=voice,
                        stream=False,
                    )
                )
            except Exception:  # noqa: BLE001
                logger.exception("预热失败（不阻断启动）")

    def _pick_voice(self, voice_id: str | None, *, explicit: bool = True) -> str:
        """选择音色。

        **显式指定的音色不存在时必须报错，不能静默回退到默认。**

        早期版本对未知 voice_id 直接回退到 default_voice_id，
        表现为：调用方指定「角色A」却听到「角色B」的声音，且 HTTP 200。
        这是典型的静默失效 —— 不报错、不崩溃，但结果是错的。
        （对比 StubEngine 一直返回 400，两者行为不一致也是缺陷。）

        :param voice_id: 请求里的音色标识
        :param explicit: 调用方是否**显式**传了该字段。
            为 False 时用引擎配置的默认音色。
            为什么要这个参数：``SynthesisRequest.voice_id`` 的默认值是
            ``"elysia"``（契约要求，不能改成 None），所以「没传」和
            「传了 elysia」在值上无法区分。引擎必须靠
            ``req.model_fields_set`` 判断，否则配置默认音色与 Schema
            默认值不一致时会误报「未知音色」。
        """
        from ..core.errors import InvalidRequestError

        if not self._voices:
            raise InvalidRequestError("没有任何可用音色，请先注册声音档案")

        # 未显式指定：用配置的默认音色，配置值不存在则取第一个可用
        if not explicit or voice_id is None or not voice_id.strip():
            if self._config.default_voice_id in self._voices:
                return self._config.default_voice_id
            return next(iter(self._voices))

        vid = voice_id.strip()
        if vid in self._voices:
            return vid

        raise InvalidRequestError(
            f"未知音色 {vid!r}，可用: {sorted(self._voices)}",
            detail={"voice_id": vid, "available": sorted(self._voices)},
        )

    def is_ready(self) -> bool:
        return self._ready

    def list_voices(self) -> list[str]:
        return list(self._voices)

    def _resolve_expression(self, req: SynthesisRequest) -> "ExpressionParams":
        """把请求里的字段解析成结构化表达参数。

        优先级：instruction（自然语言）> 显式字段 > 默认值。
        emotion/style 只作元数据，不参与引擎调用。
        """
        from ..expression import build_params, merge_params, parse_instruction

        # 基础参数来自显式字段
        base = build_params(
            speed=req.speed,
            volume=req.volume,
            pause_profile=req.pause_profile,
            emotion=req.emotion,
            intensity=req.intensity,
            style=req.style,
            source="fields",
        )

        # 自然语言指令覆盖显式字段
        if req.instruction:
            parsed = parse_instruction(req.instruction)
            base = merge_params(base, parsed.params)

        return base

    def _synthesize_segments(
        self,
        voice: str,
        info: dict[str, Any],
        text: str,
        expr: "ExpressionParams",
    ) -> tuple[bytes, float, int]:
        """按停顿档位分段合成并拼接。

        :return: (PCM 字节, 音频时长秒, 段数)
        """
        from ..expression.pause import (
            apply_gain,
            build_segments,
            concat_with_pauses,
        )

        pause_ms = PAUSE_PROFILES[expr.pause_profile.value]
        segments = build_segments(text, pause_ms)

        if not segments:
            raise InvalidRequestError("文本为空，无法合成")

        parts: list[bytes] = []
        audio_parts: list[Any] = []
        pauses: list[int] = []

        for seg in segments:
            self._reset_hop_len()
            try:
                chunks = list(
                    self._model.inference_zero_shot(
                        seg.text,
                        "",
                        info["audio_path"],
                        zero_shot_spk_id=voice,
                        stream=False,
                        speed=expr.speed,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                raise SynthesisError(
                    f"合成失败（段 {seg.text[:20]!r}）：{type(exc).__name__}: {exc}",
                    detail={"voice_id": voice, "segment": seg.text[:40]},
                ) from exc

            if not chunks:
                raise SynthesisError(
                    f"模型未返回音频（段 {seg.text[:20]!r}）",
                    detail={"voice_id": voice},
                )

            speech = chunks[0]["tts_speech"]
            pcm = _to_pcm16(speech)
            parts.append(pcm)
            audio_parts.append(
                np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
            )
            pauses.append(seg.trailing_pause_ms)

        if len(audio_parts) == 1:
            merged = audio_parts[0]
        else:
            merged = concat_with_pauses(audio_parts, pauses, SAMPLE_RATE)

        # 音量增益（音频层，不依赖模型）
        merged = apply_gain(merged, expr.volume)

        pcm_out = np.round(
            np.clip(merged, -1.0, 1.0) * 32767.0
        ).astype("<i2").tobytes()
        duration = len(pcm_out) / 2 / SAMPLE_RATE
        return pcm_out, duration, len(segments)

    def synthesize(self, req: SynthesisRequest) -> SynthesisResult:
        if not self._ready:
            raise EngineNotReadyError("引擎尚未加载完成，请稍后重试")

        voice = self._pick_voice(
            req.voice_id, explicit="voice_id" in req.model_fields_set
        )
        info = self._voices[voice]

        expr = self._resolve_expression(req)
        for w in expr.warnings:
            logger.warning(
                "表达参数警告",
                extra={"extra_fields": {"warning": w, "source": expr.source}},
            )

        ratio = self._check_text_ratio(req.text, info["prompt_text"])

        started = time.perf_counter()
        pcm, duration, n_seg = self._synthesize_segments(
            voice, info, req.text, expr
        )
        total_ms = (time.perf_counter() - started) * 1000
        self._refresh_vram()

        logger.info(
            "合成完成",
            extra={
                "extra_fields": {
                    "voice_id": voice,
                    "chars": len(req.text),
                    "duration_s": round(duration, 3),
                    "total_ms": round(total_ms, 1),
                    "rtf": round((total_ms / 1000) / duration, 3) if duration else 0,
                    "segments": n_seg,
                    "text_ratio": round(ratio, 3),
                    # 生效的控制项
                    **expr.effective_controls(),
                    # 仅元数据
                    "emotion_meta": expr.emotion,
                }
            },
        )

        return SynthesisResult(
            request_id="",
            sample_rate=SAMPLE_RATE,
            channels=1,
            duration_s=duration,
            total_ms=total_ms,
            first_audio_ms=total_ms,
            rtf=(total_ms / 1000) / duration if duration > 0 else 0.0,
            engine=self.kind,
            voice_id=voice,
            wav_bytes=_to_wav(pcm, SAMPLE_RATE),
            peak_vram_mb=self._peak_vram_mb,
        )

    def synthesize_stream(self, req: SynthesisRequest) -> Iterator[AudioChunk]:
        """真流式合成：边生成边 yield 音频块。

        实测收益（2026-11-16，zjy14 环境，每组 3 次中位数）：
          短句 10 字：非流式首包 1.60s vs 流式 1.67s（-4.4%，无收益）
          中句 22 字：非流式 3.11s vs 流式 1.72s（+44.8%）
          长句 44 字：非流式 5.36s vs 流式 1.48s（+72.4%）

        机制：上游每攒够 token_hop_len(25) + pre_lookahead_len(3) = 28 token
        就出一包，约 1.12 秒音频。所以短句等第一包时已接近生成完。

        因此调用方应先用 StreamPolicy.should_stream() 判断是否值得走流式；
        本方法不做路由判断，直接按流式执行。
        """
        if not self._ready:
            raise EngineNotReadyError("引擎尚未加载完成，请稍后重试")

        voice = self._pick_voice(
            req.voice_id, explicit="voice_id" in req.model_fields_set
        )
        info = self._voices[voice]
        expr = self._resolve_expression(req)

        self._check_text_ratio(req.text, info["prompt_text"])

        # 流式不支持分句停顿（分句需要多次独立合成，与流式冲突）。
        # 若请求要求停顿控制，降级为「分句 + 逐段流式」，段间插入静音。
        from ..expression.pause import apply_gain, build_segments
        from ..expression.control import PAUSE_PROFILES

        pause_ms = PAUSE_PROFILES[expr.pause_profile.value]
        segments = build_segments(req.text, pause_ms)

        seq = 0
        for si, seg in enumerate(segments):
            self._reset_hop_len()
            try:
                it = self._model.inference_zero_shot(
                    seg.text,
                    "",
                    info["audio_path"],
                    zero_shot_spk_id=voice,
                    stream=True,
                    speed=expr.speed,
                )
            except Exception as exc:  # noqa: BLE001
                raise SynthesisError(
                    f"流式合成失败：{type(exc).__name__}: {exc}",
                    detail={"voice_id": voice, "segment": seg.text[:40]},
                ) from exc

            for upstream in it:
                speech = upstream["tts_speech"]
                pcm = _to_pcm16(speech)
                if not pcm:
                    continue
                # 音量增益在音频层做（不依赖模型）
                if abs(expr.volume - 1.0) > 1e-6:
                    arr = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
                    arr = apply_gain(arr, expr.volume)
                    pcm = np.round(
                        np.clip(arr, -1.0, 1.0) * 32767.0
                    ).astype("<i2").tobytes()
                yield AudioChunk(
                    sequence=seq,
                    is_final=False,
                    pcm=pcm,
                    sample_rate=SAMPLE_RATE,
                    channels=1,
                )
                seq += 1

            # 段间插入静音（停顿控制）
            if seg.trailing_pause_ms > 0:
                silence = b"\x00\x00" * int(
                    SAMPLE_RATE * seg.trailing_pause_ms / 1000
                )
                yield AudioChunk(
                    sequence=seq,
                    is_final=False,
                    pcm=silence,
                    sample_rate=SAMPLE_RATE,
                    channels=1,
                )
                seq += 1

        # 结束标记
        yield AudioChunk(
            sequence=seq,
            is_final=True,
            pcm=b"",
            sample_rate=SAMPLE_RATE,
            channels=1,
        )

    def stats(self) -> EngineStats:
        from .gpu_info import gpu_info, torch_version

        info = gpu_info()
        return EngineStats(
            kind=self.kind,
            ready=self._ready,
            sample_rate=SAMPLE_RATE,
            device=self._config.device,
            gpu_name=str(info.get("device_name", "")) if info.get("ok") else "",
            torch_version=torch_version(),
            load_s=self._load_s,
            warmup_s=self._warmup_s,
            voices=self.list_voices(),
            peak_vram_mb=self._peak_vram_mb,
        )

    def diagnostics(self) -> dict[str, Any]:
        """额外诊断信息，供 /health 与 /system/status 展示。"""
        return {
            "model_dir": str(self._model_dir),
            "text_frontend": self._text_frontend,
            "voice_count": len(self._voices),
            "hop_len_resets": self._hop_resets,
            "ratio_warnings": self._ratio_warnings,
            "precision": self._config.precision.value,
            "peak_vram_mb": self._peak_vram_mb,
            # torch.compile 状态：区分「配置要求」与「实际生效」
            "compile_requested": self._config.enable_compile,
            "compile_applied": self._compile_applied,
        }

    def shutdown(self) -> None:
        self._ready = False
        self._model = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        logger.info("CosyVoice3 引擎已关闭")


__all__ = [
    "DEFAULT_MODEL_DIR",
    "NORMAL_CHARS_PER_SEC",
    "PROMPT_PREFIX",
    "REF_TEXT_RATIO_MAX",
    "REF_TEXT_RATIO_MIN",
    "SAMPLE_RATE",
    "CosyVoiceEngine",
    "check_reference_consistency",
]
