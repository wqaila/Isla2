"""应用级共享状态。

独立成模块，避免 app.py 与 routes/ 之间的循环导入。
"""

from __future__ import annotations

import time
from typing import Any

from ..config.schema import GPUConfig
from ..db.session import Database
from ..inference.engine import TTSEngine


class AppState:
    """应用级共享状态。挂在 ``app.state.zjy14`` 上。"""

    def __init__(
        self,
        config: GPUConfig,
        engine: TTSEngine,
        db: Database,
        speaker_cache: Any | None = None,
        scheduler: Any | None = None,
        cleanup: Any | None = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.db = db
        self.speaker_cache = speaker_cache
        self.scheduler = scheduler
        self.cleanup = cleanup
        self.started_at = time.time()


__all__ = ["AppState"]
