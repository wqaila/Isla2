"""
TTS 包 —— 语音合成的统一入口。

用法::

    from tts import get_controller

    ctrl = get_controller()
    result = await ctrl.synthesize("你好呀", engine="auto")
    # result.path / result.mime / result.engine / result.cached

架构：``TTSEngine``（接口） ← ``adapters/*``（各引擎实现）
      ← ``TTSController``（选引擎 / 降级 / 缓存） ← API 与面板

将来接 zjy10 的 GPT-SoVITS 时，只需在 ``adapters/`` 下加一个 Adapter，
再把它加进 ``controller.DEFAULT_PRIORITY`` 即可，上层不用改。
"""
from __future__ import annotations

from pathlib import Path

from .base import AudioResult, TTSEngine
from .controller import DEFAULT_PRIORITY, TTSController, cache_key

__all__ = [
    "AudioResult",
    "TTSEngine",
    "TTSController",
    "DEFAULT_PRIORITY",
    "cache_key",
    "get_controller",
]

_controller: TTSController | None = None


def get_controller(cache_dir: Path | None = None) -> TTSController:
    """取全局 Controller（懒加载单例）。

    单例是必要的：引擎实例与音色缓存都要跨请求复用，
    每次请求新建会反复探测引擎、反复拉音色列表。
    """
    global _controller
    if _controller is None:
        if cache_dir is None:
            from config import BASE_DIR
            cache_dir = Path(BASE_DIR) / "data" / "tts_cache"
        _controller = TTSController(cache_dir=cache_dir)
    return _controller
