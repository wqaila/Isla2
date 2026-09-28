"""edge-tts 适配器 —— 在线，质量好，默认首选。

用的是微软 Edge 的公开语音服务（**不需要 API Key**），中文女声相当自然，
比 SAPI 强很多。代价是**需要联网**。

> 这不是「爱莉希雅的声音」—— 那是 zjy10 / GPT-SoVITS 的目标。
> 这里给的是「能说话且不难听」，等 GPT-SoVITS 接上后可以整体替换。
"""
from __future__ import annotations

from pathlib import Path

from ..base import AudioResult, TTSEngine

#: 默认音色：中文女声，语气自然。可被配置项 tts_voice 覆盖。
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

#: 面板下拉里优先展示的中文音色（顺序即展示顺序）
_PREFERRED = [
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-XiaoyiNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YunyangNeural",
    "zh-CN-YunjianNeural",
    "zh-CN-liaoning-XiaobeiNeural",
    "zh-CN-shaanxi-XiaoniNeural",
]


def _import_edge():
    """延迟导入：没装这个包时模块仍能正常加载，只是 is_available() 返回 False。"""
    import edge_tts  # noqa: F401
    return edge_tts


class EdgeTtsEngine(TTSEngine):
    """微软 Edge 在线语音合成。"""

    name = "edge"
    label = "edge-tts（在线，质量好）"
    ext = "mp3"
    mime = "audio/mpeg"
    online = True

    @classmethod
    def is_available(cls) -> bool:
        """装了包就算可用。

        ⚠️ 这里**不探测网络** —— 那要几秒且可能被防火墙卡住。
        真正的网络失败会在 :meth:`synthesize` 里暴露，由 Controller 降级到 SAPI。
        """
        try:
            _import_edge()
            return True
        except Exception:
            return False

    @classmethod
    async def list_voices(cls) -> list[dict]:
        if not cls.is_available():
            return []
        try:
            edge_tts = _import_edge()
            voices = await edge_tts.list_voices()
        except Exception:
            # 拿不到列表（多半是没网）就给一份静态的中文音色，至少能选
            return [{"id": v, "label": v} for v in _PREFERRED]

        by_id = {}
        for v in voices or []:
            sid = v.get("ShortName") or ""
            if not sid:
                continue
            locale = v.get("Locale", "")
            gender = v.get("Gender", "")
            g = {"Female": "女", "Male": "男"}.get(gender, gender)
            by_id[sid] = {"id": sid, "label": f"{sid}（{locale} {g}）"}

        # 常用中文音色排前面，其余按 id 排序跟在后面
        head = [by_id[v] for v in _PREFERRED if v in by_id]
        tail = [by_id[k] for k in sorted(by_id) if k not in set(_PREFERRED)]
        return head + tail

    async def synthesize(self, text: str, voice: str = "", speed: float = 1.0,
                         out_path: Path | None = None) -> AudioResult:
        if not text.strip():
            raise ValueError("文本为空，无法合成")

        edge_tts = _import_edge()

        if out_path is None:
            import os
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="tts_edge_")
            os.close(fd)
            out_path = Path(tmp)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # edge-tts 的 rate 是百分比字符串
        pct = int(round((float(speed) - 1.0) * 100))
        rate = f"{'+' if pct >= 0 else ''}{pct}%"

        comm = edge_tts.Communicate(text, voice or DEFAULT_VOICE, rate=rate)
        await comm.save(str(out_path))

        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError("edge-tts 未产出音频（多半是网络问题）")

        # MP3 时长要解帧头才能拿，不为此引依赖 —— 留 0，
        # 播放端（浏览器）自己知道时长。
        return AudioResult(path=out_path, mime=self.mime, engine=self.name, duration_ms=0)
