"""CosyVoiceEngine 测试。

策略：
- 不加载真模型（10 秒 + 3.4GB 显存，CI 上不可行）
- 用假模型对象替换 ``CosyVoice3``，验证引擎的包装逻辑：
  token_hop_len 重置、音色注册、文本长度比检查、错误映射
- 真模型的端到端验证由 probe 脚本与手工验收覆盖
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from custom_voice.config.schema import GPUConfig, TextFrontendMode
from custom_voice.core.errors import (
    EngineNotReadyError,
    InvalidRequestError,
    SynthesisError,
)
from custom_voice.inference.engine import TTSEngine
from custom_voice.schemas.synthesis import SynthesisRequest


# ----------------------------------------------------------------------
# 假模型
# ----------------------------------------------------------------------


class _FakeInnerModel:
    """模拟 CosyVoice3Model，只提供 token_hop_len。"""

    def __init__(self) -> None:
        self.token_hop_len = 25

    def tts(self, **kwargs: Any):  # noqa: ANN201
        yield {"tts_speech": _fake_speech(0.5)}


def _fake_speech(seconds: float):
    """构造一个假 torch tensor（只需支持 detach/cpu/float/numpy/shape）。"""
    import torch

    n = int(seconds * 24000)
    return torch.zeros(1, n, dtype=torch.float32)


class _FakeFrontend:
    def __init__(self) -> None:
        self.text_frontend = ""


class _FakeCosyVoice3:
    """模拟上游 CosyVoice3 的公开接口。"""

    instances: list["_FakeCosyVoice3"] = []
    fail_load = False
    fail_synth = False

    def __init__(self, model_dir: str, fp16: bool = False) -> None:
        if _FakeCosyVoice3.fail_load:
            raise RuntimeError("模拟加载失败")
        self.model_dir = model_dir
        self.fp16 = fp16
        self.model = _FakeInnerModel()
        self.frontend = _FakeFrontend()
        self.sample_rate = 24000
        self.registered: dict[str, dict[str, str]] = {}
        self.calls: list[dict[str, Any]] = []
        _FakeCosyVoice3.instances.append(self)

    def add_zero_shot_spk(self, prompt_text: str, prompt_wav: str, spk_id: str) -> bool:
        self.registered[spk_id] = {
            "prompt_text": prompt_text,
            "audio_path": prompt_wav,
        }
        return True

    def inference_zero_shot(self, tts_text: str, prompt_text: str, prompt_wav: str,
                            zero_shot_spk_id: str = "", stream: bool = False,
                            speed: float = 1.0):  # noqa: ANN201
        if _FakeCosyVoice3.fail_synth:
            raise RuntimeError("模拟合成失败")
        self.calls.append({
            "tts_text": tts_text,
            "zero_shot_spk_id": zero_shot_spk_id,
            "speed": speed,
        })
        yield {"tts_speech": _fake_speech(max(0.2, len(tts_text) * 0.1))}


@pytest.fixture()
def fake_cosyvoice(monkeypatch, tmp_path: Path):
    """注入假 cosyvoice 模块，避免导入真模型。"""
    _FakeCosyVoice3.instances = []
    _FakeCosyVoice3.fail_load = False
    _FakeCosyVoice3.fail_synth = False

    mod = types.ModuleType("cosyvoice.cli.cosyvoice")
    mod.CosyVoice3 = _FakeCosyVoice3  # type: ignore[attr-defined]

    pkg = types.ModuleType("cosyvoice")
    cli = types.ModuleType("cosyvoice.cli")
    monkeypatch.setitem(sys.modules, "cosyvoice", pkg)
    monkeypatch.setitem(sys.modules, "cosyvoice.cli", cli)
    monkeypatch.setitem(sys.modules, "cosyvoice.cli.cosyvoice", mod)

    # 假模型目录
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    yield model_dir


@pytest.fixture()
def config(tmp_path: Path, monkeypatch) -> GPUConfig:
    """引擎配置：cpu + 关闭文本前端（避免依赖本地 fst）。"""
    monkeypatch.setenv("ZJY14_DATA_DIR", str(tmp_path))
    from custom_voice.config.loader import load_config

    return load_config(
        overrides={
            "engine": "cosyvoice3",
            "device": "cpu",
            "memory_safety_margin_mb": 0,
            "text_frontend": "none",
            "token_hop_len_reset": True,
        }
    )


@pytest.fixture()
def engine(fake_cosyvoice, config, tmp_path: Path, monkeypatch):
    """构造并加载引擎，音色用临时文件模拟。"""
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine

    # 造一个假参考音频 + 文本目录，供 _load_voices_from_dir 回退使用
    voices = tmp_path / "voices" / "testvoice"
    voices.mkdir(parents=True)
    (voices / "ref_test.wav").write_bytes(b"RIFF....WAVE")
    (voices / "ref_test.txt").write_text("这是参考音频的文本。", encoding="utf-8")

    import custom_voice.core.paths as paths_mod

    monkeypatch.setattr(paths_mod, "REFERENCE_AUDIO_DIR", tmp_path / "voices")

    cfg = config.model_copy(update={"model_dir": str(fake_cosyvoice)})
    e = CosyVoiceEngine(cfg)
    e.load()
    return e


# ----------------------------------------------------------------------
# 协议与加载
# ----------------------------------------------------------------------


def test_satisfies_protocol(engine) -> None:  # type: ignore[no-untyped-def]
    """必须满足 TTSEngine 协议，路由层才能无感知切换。"""
    assert isinstance(engine, TTSEngine)


def test_kind_is_cosyvoice3(engine) -> None:  # type: ignore[no-untyped-def]
    assert engine.kind == "cosyvoice3"


def test_load_is_idempotent(engine) -> None:  # type: ignore[no-untyped-def]
    first = engine.stats().load_s
    engine.load()
    assert engine.stats().load_s == first


def test_model_dir_missing_raises(config, tmp_path: Path) -> None:
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine

    cfg = config.model_copy(update={"model_dir": str(tmp_path / "nope")})
    e = CosyVoiceEngine(cfg)
    with pytest.raises(EngineNotReadyError) as ei:
        e.load()
    assert "模型目录不存在" in str(ei.value)


def test_load_failure_maps_to_not_ready(fake_cosyvoice, config) -> None:
    """上游加载异常必须转成 EngineNotReadyError，不泄露原始堆栈给调用方。"""
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine

    _FakeCosyVoice3.fail_load = True
    cfg = config.model_copy(update={"model_dir": str(fake_cosyvoice)})
    e = CosyVoiceEngine(cfg)
    with pytest.raises(EngineNotReadyError) as ei:
        e.load()
    assert "模型加载失败" in str(ei.value)


def test_no_voices_raises(fake_cosyvoice, config, tmp_path: Path, monkeypatch) -> None:
    """没有任何可用音色时必须报错，而不是静默启动一个不能用的服务。"""
    import custom_voice.core.paths as paths_mod
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine

    monkeypatch.setattr(paths_mod, "REFERENCE_AUDIO_DIR", tmp_path / "empty")
    cfg = config.model_copy(update={"model_dir": str(fake_cosyvoice)})
    e = CosyVoiceEngine(cfg)
    with pytest.raises(EngineNotReadyError) as ei:
        e.load()
    assert "没有可用的音色" in str(ei.value)


# ----------------------------------------------------------------------
# 音色注册
# ----------------------------------------------------------------------


def test_voice_registered_with_prefix(engine, fake_cosyvoice) -> None:  # type: ignore[no-untyped-def]
    """注册音色时必须补 <|endofprompt|> 前缀，否则上游卷积报错。"""
    inst = _FakeCosyVoice3.instances[-1]
    assert "testvoice" in inst.registered
    assert "<|endofprompt|>" in inst.registered["testvoice"]["prompt_text"]


def test_list_voices(engine) -> None:  # type: ignore[no-untyped-def]
    assert "testvoice" in engine.list_voices()


# ----------------------------------------------------------------------
# token_hop_len 重置（关键 bug 修复）
# ----------------------------------------------------------------------


def test_hop_len_reset_before_synth(engine, fake_cosyvoice) -> None:  # type: ignore[no-untyped-def]
    """每次合成前必须把 token_hop_len 重置为 25。

    上游该属性跨调用翻倍且不重置，导致首包 1.75s -> 2.25s。
    """
    inst = _FakeCosyVoice3.instances[-1]
    inst.model.token_hop_len = 100  # 模拟被污染

    engine.synthesize(SynthesisRequest(text="测试文本" * 5, voice_id="testvoice"))

    assert inst.model.token_hop_len == 25


def test_hop_len_reset_disabled(fake_cosyvoice, config) -> None:
    """关闭配置时不重置，验证配置真的生效。"""
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine

    cfg = config.model_copy(
        update={"model_dir": str(fake_cosyvoice), "token_hop_len_reset": False}
    )
    e = CosyVoiceEngine(cfg)
    e.load()
    inst = _FakeCosyVoice3.instances[-1]
    inst.model.token_hop_len = 100
    e.synthesize(SynthesisRequest(text="测试" * 5, voice_id="testvoice"))
    assert inst.model.token_hop_len == 100


# ----------------------------------------------------------------------
# 文本长度比检查
# ----------------------------------------------------------------------


def test_ratio_warning_counted(engine) -> None:  # type: ignore[no-untyped-def]
    """合成文本远短于参考文本时，应记录警告计数。"""
    before = engine.diagnostics()["ratio_warnings"]
    # 参考文本 9 字，合成文本 2 字，比值 0.22 < 0.5
    engine.synthesize(SynthesisRequest(text="短", voice_id="testvoice"))
    after = engine.diagnostics()["ratio_warnings"]
    assert after > before


def test_ratio_no_warning_when_long_enough(engine) -> None:  # type: ignore[no-untyped-def]
    before = engine.diagnostics()["ratio_warnings"]
    engine.synthesize(
        SynthesisRequest(text="这是一段足够长的合成文本用来避免长度比警告" * 2,
                         voice_id="testvoice")
    )
    after = engine.diagnostics()["ratio_warnings"]
    assert after == before


# ----------------------------------------------------------------------
# 合成
# ----------------------------------------------------------------------


def test_synthesize_returns_wav(engine) -> None:  # type: ignore[no-untyped-def]
    r = engine.synthesize(SynthesisRequest(text="你好呀今天天气不错", voice_id="testvoice"))
    assert r.wav_bytes[:4] == b"RIFF"
    assert r.sample_rate == 24000
    assert r.channels == 1
    assert r.engine == "cosyvoice3"
    assert r.duration_s > 0


def test_synthesize_unknown_voice_falls_back(engine) -> None:  # type: ignore[no-untyped-def]
    """未知音色回退到默认，而不是直接报错（决策：安全默认值）。"""
    r = engine.synthesize(SynthesisRequest(text="测试" * 5, voice_id="nobody"))
    assert r.voice_id in engine.list_voices()


def test_synthesize_not_ready(config, fake_cosyvoice) -> None:
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine

    cfg = config.model_copy(update={"model_dir": str(fake_cosyvoice)})
    e = CosyVoiceEngine(cfg)
    with pytest.raises(EngineNotReadyError):
        e.synthesize(SynthesisRequest(text="测试"))


def test_synth_failure_maps_to_synthesis_error(engine) -> None:  # type: ignore[no-untyped-def]
    _FakeCosyVoice3.fail_synth = True
    with pytest.raises(SynthesisError) as ei:
        engine.synthesize(SynthesisRequest(text="测试" * 5, voice_id="testvoice"))
    assert "合成失败" in str(ei.value)


def test_stream_yields_single_chunk(engine) -> None:  # type: ignore[no-untyped-def]
    chunks = list(engine.synthesize_stream(
        SynthesisRequest(text="测试" * 5, voice_id="testvoice")))
    assert len(chunks) == 1
    assert chunks[0].is_final is True
    assert chunks[0].sample_rate == 24000


# ----------------------------------------------------------------------
# 诊断与关闭
# ----------------------------------------------------------------------


def test_diagnostics_shape(engine) -> None:  # type: ignore[no-untyped-def]
    d = engine.diagnostics()
    for key in ("model_dir", "text_frontend", "voice_count",
                "hop_len_resets", "ratio_warnings", "precision"):
        assert key in d


def test_stats_reflects_state(engine) -> None:  # type: ignore[no-untyped-def]
    s = engine.stats()
    assert s.ready is True
    assert s.kind == "cosyvoice3"
    assert s.sample_rate == 24000
    assert "testvoice" in s.voices


def test_shutdown_marks_not_ready(engine) -> None:  # type: ignore[no-untyped-def]
    engine.shutdown()
    assert engine.is_ready() is False


# ----------------------------------------------------------------------
# 文本前端配置
# ----------------------------------------------------------------------


def test_text_frontend_none_mode(fake_cosyvoice, config) -> None:
    """none 模式不应尝试加载 wetext。"""
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine

    cfg = config.model_copy(
        update={"model_dir": str(fake_cosyvoice), "text_frontend": "none"}
    )
    e = CosyVoiceEngine(cfg)
    e.load()
    assert e.diagnostics()["text_frontend"] == ""


def test_text_frontend_wetext_mode_fails_loudly(
    fake_cosyvoice, config, monkeypatch, tmp_path: Path
) -> None:
    """wetext 强制模式：资源缺失必须启动报错，不能静默降级。"""
    import custom_voice.inference.text_frontend as tf
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine

    monkeypatch.setattr(tf, "DEFAULT_WETEXT_CACHE", tmp_path / "no-fst")
    cfg = config.model_copy(
        update={"model_dir": str(fake_cosyvoice), "text_frontend": "wetext"}
    )
    e = CosyVoiceEngine(cfg)
    with pytest.raises(tf.TextFrontendError):
        e.load()


# ----------------------------------------------------------------------
# 参考文本一致性校验（拦截真实踩过的坑）
# ----------------------------------------------------------------------


def _make_wav(path: Path, seconds: float, sr: int = 24000) -> Path:
    import soundfile as sf

    n = int(seconds * sr)
    data = (np.sin(2 * np.pi * 220 * np.arange(n) / sr) * 0.3).astype(np.float32)
    sf.write(str(path), data, sr, subtype="PCM_16")
    return path


def test_ratio_check_passes_for_matching_text(tmp_path: Path) -> None:
    """文本与音频时长匹配时应通过。8 秒音频约 34 字。"""
    from custom_voice.inference.cosyvoice_engine import check_reference_consistency

    wav = _make_wav(tmp_path / "ok.wav", 8.0)
    text = "You are a helpful assistant.<|endofprompt|>" + "一二三四五六七八九十" * 3 + "一二三四"
    ok, msg = check_reference_consistency(str(wav), text)
    assert ok is True, msg


def test_ratio_check_rejects_incomplete_text(tmp_path: Path) -> None:
    """复现真实踩过的坑：4.5 秒音频只标 11 字。

    这正是 1.6.mp3 的情况，也是导致
    'Kernel size can't be greater than actual input size' 的根因。
    """
    from custom_voice.inference.cosyvoice_engine import check_reference_consistency

    wav = _make_wav(tmp_path / "short_text.wav", 4.5)
    text = "You are a helpful assistant.<|endofprompt|>晚上好，如此美妙的夜晚"
    ok, msg = check_reference_consistency(str(wav), text)
    assert ok is False
    assert "疑似不完整" in msg
    assert "Kernel size" in msg  # 错误信息里要提示后果


def test_ratio_check_rejects_too_much_text(tmp_path: Path) -> None:
    from custom_voice.inference.cosyvoice_engine import check_reference_consistency

    wav = _make_wav(tmp_path / "long_text.wav", 2.0)
    text = "You are a helpful assistant.<|endofprompt|>" + "一二三四五六七八九十" * 5
    ok, msg = check_reference_consistency(str(wav), text)
    assert ok is False
    assert "疑似过多" in msg


def test_ratio_check_rejects_empty_body(tmp_path: Path) -> None:
    from custom_voice.inference.cosyvoice_engine import check_reference_consistency

    wav = _make_wav(tmp_path / "x.wav", 5.0)
    ok, msg = check_reference_consistency(
        str(wav), "You are a helpful assistant.<|endofprompt|>"
    )
    assert ok is False
    assert "为空" in msg


def test_ratio_check_tolerates_missing_audio(tmp_path: Path) -> None:
    """读不到音频时应跳过校验，而不是阻断启动。"""
    from custom_voice.inference.cosyvoice_engine import check_reference_consistency

    ok, _ = check_reference_consistency(str(tmp_path / "nope.wav"), "文本" * 10)
    assert ok is True


def test_bad_reference_voice_is_skipped(fake_cosyvoice, config, tmp_path: Path,
                                        monkeypatch) -> None:
    """参考文本不完整的音色应被跳过，且不阻断其他音色注册。"""
    import custom_voice.core.paths as paths_mod
    from custom_voice.inference.cosyvoice_engine import CosyVoiceEngine

    voices = tmp_path / "voices"
    # 坏音色：4.5 秒音频只标 11 字
    bad = voices / "bad"
    bad.mkdir(parents=True)
    _make_wav(bad / "ref_bad.wav", 4.5)
    (bad / "ref_bad.txt").write_text("晚上好，如此美妙的夜晚", encoding="utf-8")
    # 好音色：8 秒音频约 34 字
    good = voices / "good"
    good.mkdir(parents=True)
    _make_wav(good / "ref_good.wav", 8.0)
    (good / "ref_good.txt").write_text("一二三四五六七八九十" * 3 + "一二三四",
                                       encoding="utf-8")

    monkeypatch.setattr(paths_mod, "REFERENCE_AUDIO_DIR", voices)
    cfg = config.model_copy(update={"model_dir": str(fake_cosyvoice)})
    e = CosyVoiceEngine(cfg)
    e.load()

    assert "good" in e.list_voices()
    assert "bad" not in e.list_voices()
