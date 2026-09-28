"""
TTS 抽象层 —— 统一各语音合成引擎的接口。

设计原则（对应 zjy10 规划的 Model Controller + Adapter）：

1. **上层只认接口**：API 与面板只依赖 :class:`TTSEngine`，不关心底下是
   Windows SAPI、edge-tts 还是将来的 GPT-SoVITS。
2. **引擎可插拔**：新增一个引擎 = 加一个 Adapter，不用改上层。
3. **必须能失败降级**：首选引擎不可用（没装 / 没网 / GPU 挂了）就自动落到下一个，
   而不是直接报错给用户。

当前内置的引擎（按推荐优先级）：

===========  ======  ==========================================
引擎         联网    定位
===========  ======  ==========================================
``edge``     需要    质量好，中文女声自然 —— **默认首选**
``sapi``     不需要  系统自带、零依赖，音色机械 —— **兜底**
===========  ======  ==========================================

> 将来接 GPT-SoVITS（zjy10）时，只需再加一个 Adapter，
> 并在 :mod:`tts.controller` 的默认优先级里插进去即可。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AudioResult:
    """一次合成的产物描述。"""

    path: Path
    """音频文件落盘路径"""

    mime: str
    """MIME 类型（不同引擎产物格式不同：SAPI 出 wav，edge-tts 出 mp3）"""

    engine: str = ""
    """实际干活的引擎标识"""

    duration_ms: int = 0
    """音频时长（毫秒）。取不到就是 0。"""

    cached: bool = False
    """是否命中缓存（未重新合成）"""


class TTSEngine(ABC):
    """语音合成引擎接口。

    子类至少要给出 ``name`` / ``label`` / ``ext`` / ``mime``，
    并实现 :meth:`is_available` 与 :meth:`synthesize`。
    """

    #: 引擎标识，用于配置项 ``tts_engine`` 与日志
    name: str = "base"
    #: 人类可读名称，面板展示用
    label: str = "基础引擎"
    #: 产物扩展名（不含点）
    ext: str = "wav"
    #: 产物 MIME
    mime: str = "audio/wav"
    #: 是否需要联网
    online: bool = False

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """本机是否**真的能用**。

        ⚠️ 不要只判断 ``import`` 成功 —— 那只能说明包在，
        不代表能出声（比如缺系统组件、缺网络、缺模型文件）。
        能做实际探测就实际探测。
        """

    @abstractmethod
    async def synthesize(self, text: str, voice: str = "", speed: float = 1.0,
                         out_path: Path | None = None) -> AudioResult:
        """把文本合成为音频文件。

        :param text: 要合成的文本
        :param voice: 音色标识；空字符串表示用引擎默认
        :param speed: 语速倍率，1.0 为正常
        :param out_path: 输出路径；为 None 时由引擎自己挑临时文件
        :raises Exception: 合成失败就抛，由 :class:`~tts.controller.TTSController`
                           决定是否降级到下一个引擎
        """

    @classmethod
    async def list_voices(cls) -> list[dict]:
        """可选音色列表 ``[{id, label}]``。不支持就返回空列表。

        ⚠️ 这里刻意是 **async** 的：取音色列表往往要走网络（edge-tts），
        而同步版本在已有事件循环里没法跑 ``asyncio.run()`` ——
        FastAPI 的请求处理器就是异步的，同步实现必然踩坑。
        """
        return []
