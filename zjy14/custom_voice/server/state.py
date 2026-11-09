"""应用级共享状态。

独立成模块，避免 app.py 与 routes/ 之间的循环导入。
"""

from __future__ import annotations

import time

from ..config.schema import GPUConfig
from ..db.session import Database
from ..inference.engine import TTSEngine


class AppState:
    """应用级共享状态。挂在 ``app.state.zjy14`` 上。"""

    def __init__(self, config: GPUConfig, engine: TTSEngine, db: Database) -> None:
        self.config = config
        self.engine = engine
        self.db = db
        self.started_at = time.time()


__all__ = ["AppState"]
