"""填充音（语气词）资源与轮换策略。

## 为什么需要

用户从松手到听到回复要等约 2.3 秒（ASR 0.1s + LLM 0.8s + TTS 首块 1.5s）。
这段时间完全静默，感知上很"慢"。

真人对答时不会干等，会说「嗯…」「这个嘛…」——既填补空白，
又显得在思考。本模块提供这些预生成的语气词。

## 关键设计

**1. 资源是预生成 + 裁剪过的**

TTS 输出自带首尾静音（实测首静音最多 0.35s），直接播会先听到静音，
恰好抵消填充音的作用。所以 `tools/gen_fillers.py` 生成时会：
- 裁掉首尾静音（相对峰值 -40dB 以下）
- 加 10ms 淡入 / 80ms 淡出（避免爆音与被切断时突兀）

**2. 三轮换 + 避免连续重复**

用户确认用三个：「嗯…」「这个嘛…」「唔…」。
随机选，但记录上一次的选择，避免连着两次相同（否则听着像卡带）。

**3. 按等待时长选长度**

三个时长不同（0.70 / 0.77 / 0.69 秒），实际差别不大。
真正的适配在 `FillerSession`：不预判等待多久，而是**边播边补**——
正文没到就继续发下一个，正文一到立刻停。

## 资源格式

裸 PCM16LE 单声道 24kHz，无 WAV 头（板子直接送 I2S，不需要头）。
`fillers.json` 记录每个的 id / 时长 / 样本数。
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("tts.fillers")

#: 填充音资源目录（相对于项目根）
DEFAULT_DIR = Path(__file__).resolve().parent / "fillers"

#: 填充音采样率，必须与 TTS 输出一致（板子按这个采样率播）
SAMPLE_RATE = 24000

#: 单轮填充音总时长上限（秒）。
#: 超过就停——否则 LLM/TTS 卡住时会把三个循环播成"复读机"。
MAX_FILLER_SECONDS = 3.0

#: 单轮最多发几声填充音。
#:
#: 2026-11-19 加这个上限的原因：板子上实测会连着听到 2~3 声语气词，
#: 很不自然。根因是发送端没有跟随播放进度（一口气把 3 秒全塞给板子），
#: 已在 ws/server.py::_send_fillers 修掉。这里再加一道硬上限，
#: 保证即使等待很久，用户也不会听到一串语气词。
#:
#: 设 1 表示"最多一声"——这是真人对话里最自然的行为：思考时「嗯…」一次，
#: 然后直接说正文。要允许更长的等待表现，可调到 2。
MAX_FILLER_CLIPS = 1


@dataclass(frozen=True, slots=True)
class FillerClip:
    """一个填充音。"""

    id: str
    text: str
    pcm: bytes
    duration_s: float

    @property
    def bytes_per_sec(self) -> int:
        return SAMPLE_RATE * 2


class FillerBank:
    """填充音资源库。

    用法::

        bank = FillerBank()          # 从默认目录加载
        clip = bank.pick()           # 随机取一个（不连续重复）
        if bank.available:
            ...
    """

    def __init__(
        self,
        directory: Path | None = None,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._dir = directory or DEFAULT_DIR
        self._clips: list[FillerClip] = []
        self._last_id: str | None = None
        self._rng = rng or random.Random()
        self._load()

    # ------------------------------------------------------------------

    def _load(self) -> None:
        meta_path = self._dir / "fillers.json"
        if not meta_path.is_file():
            logger.warning(
                "填充音元数据不存在: %s（先跑 tools/gen_fillers.py）", meta_path
            )
            return
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("填充音元数据解析失败: %s", exc)
            return

        sr = int(meta.get("sample_rate", SAMPLE_RATE))
        if sr != SAMPLE_RATE:
            logger.warning(
                "填充音采样率 %d 与预期 %d 不一致，可能导致播放变调",
                sr, SAMPLE_RATE,
            )

        for item in meta.get("items", []):
            pcm_path = self._dir / str(item.get("file", ""))
            if not pcm_path.is_file():
                logger.warning("填充音文件缺失: %s", pcm_path)
                continue
            pcm = pcm_path.read_bytes()
            if not pcm:
                logger.warning("填充音为空: %s", pcm_path)
                continue
            self._clips.append(FillerClip(
                id=str(item.get("id", pcm_path.stem)),
                text=str(item.get("text", "")),
                pcm=pcm,
                duration_s=len(pcm) / 2 / SAMPLE_RATE,
            ))

        if self._clips:
            total = sum(c.duration_s for c in self._clips)
            logger.info(
                "填充音已加载 %d 个（合计 %.2fs）: %s",
                len(self._clips), total,
                ", ".join(f"{c.id}({c.duration_s:.2f}s)" for c in self._clips),
            )
        else:
            logger.warning("没有可用的填充音，将跳过填充音功能")

    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        return bool(self._clips)

    @property
    def clips(self) -> list[FillerClip]:
        return list(self._clips)

    def pick(self) -> FillerClip | None:
        """取一个填充音。

        尽量不与上一次相同——连续两次「嗯…」听起来像卡带。
        只有一个可用时只能重复。
        """
        if not self._clips:
            return None
        if len(self._clips) == 1:
            self._last_id = self._clips[0].id
            return self._clips[0]

        candidates = [c for c in self._clips if c.id != self._last_id]
        clip = self._rng.choice(candidates or self._clips)
        self._last_id = clip.id
        return clip

    def reset_rotation(self) -> None:
        """重置轮换状态（新会话时调用，避免上一会话的记忆影响）。"""
        self._last_id = None


class FillerSession:
    """一轮对话中的填充音发送状态。

    职责：决定「还要不要继续发填充音」，以及累计已发时长。

    为什么单独一个类：``_run_pipeline`` 里既有 ASR 又有 LLM 又有 TTS，
    再塞填充音逻辑会很乱。把"发多少、何时停"的状态收在这里，
    主流程只需要问 ``should_continue()`` 和 ``record()``。
    """

    def __init__(
        self,
        bank: FillerBank,
        *,
        max_seconds: float = MAX_FILLER_SECONDS,
        max_clips: int = MAX_FILLER_CLIPS,
    ):
        self._bank = bank
        self._max = max_seconds
        self._max_clips = max_clips
        self._sent_s = 0.0
        self._count = 0

    @property
    def sent_seconds(self) -> float:
        return self._sent_s

    @property
    def count(self) -> int:
        return self._count

    def should_continue(self) -> bool:
        """是否还能再发一个。

        两道限制：
        1. **声数上限** ``max_clips``（默认 1）—— 最直接的保证，
           用户不会听到一串语气词。
        2. **总时长上限** ``max_seconds`` —— 防止 LLM 卡死时无限循环。

        时长判断用「已发 + 一个典型长度」是否仍在上限内，而不是只看已发量。
        原因：每个填充音约 0.7s，若只判 ``sent < max``，
        2.93s 时仍会再发一个、变成 3.6s，超出上限 20%。
        预留一个的长度能让实际总量贴近上限而不明显超出。
        """
        if not self._bank.available:
            return False
        if self._count == 0:
            return True          # 第一个总是发（保证有即时反馈）
        # 声数上限：默认 1，发过一声就不再发
        if self._count >= self._max_clips:
            return False
        # 预留最长那个的长度，避免明显超限
        typical = max(c.duration_s for c in self._bank.clips)
        return self._sent_s + typical <= self._max

    def next_clip(self) -> FillerClip | None:
        """取下一个填充音并记账。"""
        if not self.should_continue():
            return None
        clip = self._bank.pick()
        if clip is None:
            return None
        self._sent_s += clip.duration_s
        self._count += 1
        return clip


__all__ = [
    "FillerBank",
    "FillerClip",
    "FillerSession",
    "SAMPLE_RATE",
    "MAX_FILLER_SECONDS",
    "MAX_FILLER_CLIPS",
    "DEFAULT_DIR",
]
