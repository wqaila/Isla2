"""ASR 引擎测试：环境检查逻辑与错误处理。

不需要真实 GPU 或模型——用 monkeypatch 模拟 torch 行为，
重点验证"sm_120 不匹配必须报错"这条关键逻辑。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from asr.engine import ASREngine, ASRError, CUDANotAvailableError, check_device


def _fake_torch(*, available: bool, capability: tuple[int, int], arch_list: list[str]):
    """构造一个假的 torch 模块，用于测试 check_device 的分支。"""
    torch = types.ModuleType("torch")
    torch.__version__ = "2.8.0+cu128"

    cuda = types.SimpleNamespace()
    cuda.is_available = lambda: available
    cuda.get_device_name = lambda i=0: "Fake GPU"
    cuda.get_device_capability = lambda i=0: capability
    cuda.get_arch_list = lambda: list(arch_list)
    torch.cuda = cuda

    torch.version = types.SimpleNamespace(cuda="12.8")
    return torch


@pytest.fixture()
def patch_torch(monkeypatch):
    def _apply(**kwargs):
        fake = _fake_torch(**kwargs)
        monkeypatch.setitem(sys.modules, "torch", fake)
        return fake

    return _apply


def test_sm120_matching_passes(patch_torch) -> None:
    patch_torch(available=True, capability=(12, 0), arch_list=["sm_80", "sm_90", "sm_120"])
    info = check_device(require_gpu=True)
    assert info.cuda_available is True
    assert info.arch_match is True
    assert info.using_gpu is True


def test_sm120_missing_raises(patch_torch) -> None:
    """核心用例：is_available() 为 True 但架构不含 sm_120，必须报错。

    这是本机实测踩到的坑：torch 2.1.0+cu118 在 RTX 5070 上
    is_available() 返回 True，但 arch_list 只有 sm_90 及以下。
    """
    patch_torch(
        available=True,
        capability=(12, 0),
        arch_list=["sm_37", "sm_50", "sm_80", "sm_90", "compute_37"],
    )
    with pytest.raises(CUDANotAvailableError) as exc:
        check_device(require_gpu=True)
    msg = str(exc.value)
    assert "sm_120" in msg
    assert "2.7.0" in msg or "cu128" in msg


def test_compute_arch_also_accepted(patch_torch) -> None:
    """arch_list 里出现 compute_120 也算匹配（PTX 可 JIT）。"""
    patch_torch(available=True, capability=(12, 0), arch_list=["compute_120"])
    info = check_device(require_gpu=True)
    assert info.arch_match is True


def test_cuda_unavailable_raises(patch_torch) -> None:
    patch_torch(available=False, capability=(12, 0), arch_list=[])
    with pytest.raises(CUDANotAvailableError, match="CUDA 不可用"):
        check_device(require_gpu=True)


def test_cuda_unavailable_cpu_fallback_allowed(patch_torch) -> None:
    patch_torch(available=False, capability=(12, 0), arch_list=[])
    info = check_device(require_gpu=False)
    assert info.cuda_available is False
    assert info.using_gpu is False


def test_missing_torch_raises(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(ASRError, match="未安装 PyTorch"):
        check_device(require_gpu=False)


def test_engine_load_is_idempotent(patch_torch, monkeypatch) -> None:
    """重复 load() 不应重复加载模型。"""
    patch_torch(available=True, capability=(12, 0), arch_list=["sm_120"])

    calls = {"n": 0}

    class FakeAutoModel:
        def __init__(self, **kwargs):
            calls["n"] += 1

        def generate(self, **kwargs):
            return [{"text": ""}]

    fake_funasr = types.ModuleType("funasr")
    fake_funasr.AutoModel = FakeAutoModel
    fake_funasr.__version__ = "1.4.16"
    monkeypatch.setitem(sys.modules, "funasr", fake_funasr)

    engine = ASREngine(warmup=False)
    engine.load()
    engine.load()
    assert calls["n"] == 1


def test_engine_missing_funasr(patch_torch, monkeypatch) -> None:
    patch_torch(available=True, capability=(12, 0), arch_list=["sm_120"])
    monkeypatch.setitem(sys.modules, "funasr", None)
    engine = ASREngine(warmup=False)
    with pytest.raises(ASRError, match="FunASR"):
        engine.load()


def test_engine_transcribe_missing_file(patch_torch, monkeypatch, tmp_path: Path) -> None:
    patch_torch(available=True, capability=(12, 0), arch_list=["sm_120"])

    class FakeAutoModel:
        def __init__(self, **kwargs):
            pass

        def generate(self, **kwargs):
            return [{"text": "你好"}]

    fake_funasr = types.ModuleType("funasr")
    fake_funasr.AutoModel = FakeAutoModel
    fake_funasr.__version__ = "1.4.16"
    monkeypatch.setitem(sys.modules, "funasr", fake_funasr)

    engine = ASREngine(warmup=False, cache_dir=tmp_path)
    engine.load()
    with pytest.raises(ASRError, match="不存在"):
        engine.transcribe_file(tmp_path / "nope.wav")


def test_device_info_dict_shape(patch_torch) -> None:
    patch_torch(available=True, capability=(12, 0), arch_list=["sm_120"])
    info = check_device(require_gpu=True)
    d = info.to_dict()
    assert d["capability"] == [12, 0]
    assert d["using_gpu"] is True
    assert isinstance(d["arch_list"], list)
