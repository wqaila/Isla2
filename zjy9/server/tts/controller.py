"""
TTS Controller —— 选引擎、失败降级、音频缓存。

上层（API / 面板）只跟这里打交道，不直接碰 Adapter。

职责：

1. **选引擎**：``auto`` 按优先级挑第一个可用的；指定名字就用那个
2. **失败降级**：选中的引擎合成失败（没网 / 报错），自动往下一个可用引擎落，
   并把「实际是谁干的」如实报回去 —— 不能让用户以为听到了 edge-tts，
   实际是 SAPI 在念
3. **音频缓存**：同一段文本 + 同一套参数只合成一次。
   按 ``sha256(文本|引擎|音色|语速)`` 落盘，命中直接复用。
   长回复反复播放时这是性能关键。
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from .base import AudioResult, TTSEngine

logger = logging.getLogger("tts")

#: 默认优先级：质量好的在前，兜底的在后
DEFAULT_PRIORITY = ("edge", "sapi")


def _discover_engines() -> list[type[TTSEngine]]:
    """收集内置引擎。

    导入失败（比如没装 edge-tts）不应该让整个模块挂掉 ——
    Adapter 内部是延迟导入的，所以这里直接 import 是安全的。
    """
    from .adapters.edge import EdgeTtsEngine
    from .adapters.sapi import SapiEngine

    return [EdgeTtsEngine, SapiEngine]


def cache_key(text: str, engine: str, voice: str, speed: float) -> str:
    """缓存键：文本 + 引擎 + 音色 + 语速 一起哈希。

    任一参数不同就是不同的音频，必须分开存 —— 否则换了音色却播到旧声音。
    """
    raw = f"{engine}\x00{voice}\x00{float(speed):.3f}\x00{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


class TTSController:
    """语音合成的统一入口。"""

    def __init__(self, cache_dir: Path, priority: tuple[str, ...] | None = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._classes: dict[str, type[TTSEngine]] = {
            c.name: c for c in _discover_engines()
        }
        self.priority: list[str] = [n for n in (priority or DEFAULT_PRIORITY)
                                    if n in self._classes]
        self._instances: dict[str, TTSEngine] = {}
        self._voices_cache: dict[str, tuple[float, list[dict]]] = {}

    # ---------- 引擎信息 ----------

    def _instance(self, name: str) -> TTSEngine:
        if name not in self._instances:
            cls = self._classes.get(name)
            if cls is None:
                raise KeyError(f"未知引擎：{name}")
            self._instances[name] = cls()
        return self._instances[name]

    def available(self) -> list[dict]:
        """各引擎的可用性（面板用来渲染下拉）。"""
        out = []
        for name in self.priority:
            cls = self._classes[name]
            try:
                ok = bool(cls.is_available())
            except Exception as e:      # 探测本身也可能炸，不能让面板 500
                logger.warning("引擎 %s 可用性探测失败：%s", name, e)
                ok = False
            out.append({
                "name": name,
                "label": cls.label,
                "available": ok,
                "online": cls.online,
                "mime": cls.mime,
            })
        return out

    def default_engine(self) -> str | None:
        """按优先级挑第一个可用的引擎。"""
        for item in self.available():
            if item["available"]:
                return item["name"]
        return None

    async def list_voices(self, engine: str = "", ttl: float = 600.0) -> list[dict]:
        """某引擎的可选音色。带 TTL 缓存 —— edge 取音色要走网络，别每次都拉。"""
        name = engine or self.default_engine()
        if not name:
            return []
        hit = self._voices_cache.get(name)
        if hit and (time.time() - hit[0]) < ttl:
            return hit[1]
        try:
            voices = await self._classes[name].list_voices()
        except Exception as e:
            logger.warning("取 %s 的音色列表失败：%s", name, e)
            voices = []
        self._voices_cache[name] = (time.time(), voices)
        return voices

    # ---------- 缓存 ----------

    def _lookup(self, key: str) -> Path | None:
        """按 key 找已缓存的音频文件（扩展名随引擎不同）。"""
        for p in self.cache_dir.glob(f"{key}.*"):
            if p.is_file() and p.stat().st_size > 0:
                return p
        return None

    def stats(self) -> dict:
        files = [p for p in self.cache_dir.iterdir() if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        return {"count": len(files), "bytes": total,
                "dir": str(self.cache_dir)}

    @staticmethod
    def _unlink(p: Path) -> bool:
        """删文件，返回「是否真的没了」。

        ⚠️ **不能只靠异常来判断结果**：某些环境（带删除拦截层/回收站重定向的
        沙箱）会**抛异常但文件确实被删掉了**。只按异常计数会得到
        「明明删干净了却报告删了 0 个」的假象。所以删完直接看文件还在不在。
        """
        try:
            p.unlink()
        except OSError as e:
            logger.warning("删除 %s 失败：%s", p, e)
        return not p.exists()

    def clear_cache(self) -> int:
        """清空缓存，返回**真正**删掉的文件数。"""
        removed = 0
        for p in self.cache_dir.iterdir():
            if p.is_file() and self._unlink(p):
                removed += 1
        return removed

    def _evict_if_needed(self, max_mb: int) -> None:
        """超过上限就按「最久未使用」删到 80% 以下。"""
        if max_mb <= 0:
            return
        limit = max_mb * 1024 * 1024
        files = [p for p in self.cache_dir.iterdir() if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        if total <= limit:
            return
        files.sort(key=lambda p: p.stat().st_mtime)      # 旧的在前
        target = int(limit * 0.8)
        for p in files:
            if total <= target:
                break
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if self._unlink(p):
                total -= size

    # ---------- 合成 ----------

    async def synthesize(self, text: str, engine: str = "auto", voice: str = "",
                         speed: float = 1.0, use_cache: bool = True,
                         cache_max_mb: int = 200) -> AudioResult:
        """合成语音。

        :param engine: ``auto`` 或具体引擎名
        :param voice: 音色；空表示用引擎默认
        :param speed: 语速倍率
        :param use_cache: 是否复用缓存
        :param cache_max_mb: 缓存上限（MB），超出按 LRU 清理
        :raises RuntimeError: 所有候选引擎都失败
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("文本为空，无法合成")

        avail = self.available()
        usable = [i["name"] for i in avail if i["available"]]
        if not usable:
            raise RuntimeError(
                "没有可用的 TTS 引擎。请检查：edge-tts 是否安装、"
                "或是否在 Windows 上（SAPI 依赖系统语音）"
            )

        # 决定候选顺序：指定了引擎就把它放最前（失败再往后面的落）
        if engine and engine != "auto":
            if engine not in self._classes:
                raise KeyError(f"未知引擎：{engine}")
            order = [engine] + [n for n in self.priority if n != engine]
            order = [n for n in order if n in usable]
        else:
            order = usable

        last_err: Exception | None = None
        for name in order:
            inst = self._instance(name)
            key = cache_key(text, name, voice, float(speed))
            ext = inst.ext

            if use_cache:
                hit = self._lookup(key)
                if hit:
                    logger.info("TTS 缓存命中：%s（%s）", key[:8], name)
                    return AudioResult(path=hit, mime=inst.mime, engine=name,
                                       duration_ms=0, cached=True)

            out_path = self.cache_dir / f"{key}.{ext}"
            try:
                result = await inst.synthesize(text, voice=voice,
                                               speed=speed, out_path=out_path)
            except Exception as e:
                last_err = e
                logger.warning("引擎 %s 合成失败，尝试下一个：%s", name, e)
                # 清掉可能残留的半成品
                try:
                    if out_path.exists() and out_path.stat().st_size == 0:
                        out_path.unlink()
                except OSError:
                    pass
                continue

            logger.info("TTS 合成成功：%s（%s，%d ms）",
                        key[:8], name, result.duration_ms)
            self._evict_if_needed(cache_max_mb)
            return result

        raise RuntimeError(f"所有 TTS 引擎都失败了，最后一个错误：{last_err}")
