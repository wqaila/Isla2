"""版本化 speaker 条件缓存。

核心设计（对应原提示词第 2 节原则）：
"更换参考音频、模型版本或预处理参数时使缓存失效"

缓存 key = sha256(
    speaker_id | 音频内容哈希 | 模型版本 | 预处理版本 | 指令文本
)

为什么用内容哈希而不是文件 mtime：
用户可能覆盖同名文件但内容不变，也可能改回旧内容。
用内容哈希才能准确反映"参考音频是否真的变了"。

注意：本模块缓存的是**上游 add_zero_shot_spk 注册后的 spk2info 条目**，
不是自定义的 speaker embedding。真实类型由引擎决定，见 CosyVoiceEngine（Phase 2）。
Phase 1/3 的 Stub 引擎只做占位。
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

from ..core.logging import get_logger

logger = get_logger("speaker.cache")

T = TypeVar("T")


def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    """计算文件内容哈希。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def make_cache_key(
    *,
    speaker_id: str,
    audio_sha256: str,
    model_version: str,
    preprocessing_version: str,
    prompt_text: str = "",
) -> str:
    """构造缓存 key。

    任一要素变化都会导致 key 变化，从而自动失效。
    """
    payload = json.dumps(
        {
            "speaker_id": speaker_id,
            "audio_sha256": audio_sha256,
            "model_version": model_version,
            "preprocessing_version": preprocessing_version,
            "prompt_text": prompt_text,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(slots=True)
class CacheEntry(Generic[T]):
    """一条缓存条目。"""

    key: str
    value: T
    speaker_id: str
    hits: int = 0


class SpeakerConditionCache(Generic[T]):
    """LRU 缓存。

    - 容量上限可配（configs/gpu.yaml 的 speaker_cache_max_items）
    - 命中率统计供 /api/v1/system/status 展示
    - 显式 invalidate 支持按 speaker 失效
    """

    def __init__(self, max_items: int = 8, enabled: bool = True) -> None:
        if max_items < 1:
            raise ValueError("max_items 必须 >= 1")
        self._max_items = max_items
        self._enabled = enabled
        self._store: OrderedDict[str, CacheEntry[T]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def max_items(self) -> int:
        return self._max_items

    def __len__(self) -> int:
        return len(self._store)

    def get(self, key: str) -> T | None:
        if not self._enabled:
            self._misses += 1
            return None
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None
        # LRU：命中后移到末尾
        self._store.move_to_end(key)
        entry.hits += 1
        self._hits += 1
        return entry.value

    def put(self, key: str, speaker_id: str, value: T) -> None:
        if not self._enabled:
            return
        if key in self._store:
            self._store.move_to_end(key)
            self._store[key] = CacheEntry(key, value, speaker_id)
            return

        while len(self._store) >= self._max_items:
            evicted_key, _ = self._store.popitem(last=False)
            self._evictions += 1
            logger.info(
                "缓存淘汰",
                extra={"extra_fields": {"evicted_key": evicted_key[:8]}},
            )

        self._store[key] = CacheEntry(key, value, speaker_id)

    def invalidate_speaker(self, speaker_id: str) -> int:
        """使某个 speaker 的所有缓存失效。返回清理条数。"""
        keys = [k for k, e in self._store.items() if e.speaker_id == speaker_id]
        for k in keys:
            del self._store[k]
        if keys:
            logger.info(
                "缓存已失效",
                extra={
                    "extra_fields": {"speaker_id": speaker_id, "count": len(keys)}
                },
            )
        return len(keys)

    def clear(self) -> None:
        self._store.clear()

    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "enabled": self._enabled,
            "max_items": self._max_items,
            "size": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total else 0.0,
            "evictions": self._evictions,
            "keys": [k[:8] for k in self._store],
        }


__all__ = [
    "CacheEntry",
    "SpeakerConditionCache",
    "file_sha256",
    "make_cache_key",
]
