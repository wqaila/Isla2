"""GPU 与显存探测。

放在 inference/ 层的原因：torch 属于模型运行时依赖，
server/ 层不得直接 import（由 tests/test_architecture.py 强制）。

server 通过本模块的纯函数获取信息，不感知 torch 的存在。
torch 未安装时所有函数返回结构化结果而非抛异常。
"""

from __future__ import annotations

from typing import Any

from ..core.logging import get_logger

logger = get_logger("inference.gpu_info")


def _torch() -> Any | None:
    """延迟导入 torch。不可用时返回 None。"""
    try:
        import torch
    except ImportError:
        return None
    return torch


def gpu_info() -> dict[str, Any]:
    """返回 GPU 与显存信息。

    无论环境是否具备 CUDA，都返回可序列化的 dict，不抛异常。
    """
    torch = _torch()
    if torch is None:
        return {
            "ok": False,
            "reason": "torch 未安装，无法读取 GPU 信息",
            "cuda_available": False,
        }

    if not torch.cuda.is_available():
        return {
            "ok": False,
            "reason": "CUDA 不可用",
            "cuda_available": False,
            "torch": torch.__version__,
        }

    try:
        free, total = torch.cuda.mem_get_info()
        props = torch.cuda.get_device_properties(0)
        return {
            "ok": True,
            "cuda_available": True,
            "torch": torch.__version__,
            "cuda_runtime": torch.version.cuda,
            "device_name": torch.cuda.get_device_name(0),
            "capability": list(torch.cuda.get_device_capability(0)),
            "total_mb": round(total / 1024**2),
            "free_mb": round(free / 1024**2),
            "used_mb": round((total - free) / 1024**2),
            "allocated_mb": round(torch.cuda.memory_allocated(0) / 1024**2),
            "peak_allocated_mb": round(torch.cuda.max_memory_allocated(0) / 1024**2),
            "multi_processor_count": props.multi_processor_count,
            "arch_list": torch.cuda.get_arch_list(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("读取 GPU 信息失败")
        return {
            "ok": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "cuda_available": True,
        }


def free_vram_mb() -> float | None:
    """当前空闲显存（MB）。不可用时返回 None。"""
    torch = _torch()
    if torch is None or not torch.cuda.is_available():
        return None
    try:
        free, _ = torch.cuda.mem_get_info()
        return free / 1024**2
    except Exception:  # noqa: BLE001
        return None


def torch_version() -> str:
    torch = _torch()
    return torch.__version__ if torch is not None else ""


def cuda_available() -> bool:
    torch = _torch()
    return bool(torch is not None and torch.cuda.is_available())


__all__ = [
    "cuda_available",
    "free_vram_mb",
    "gpu_info",
    "torch_version",
]
