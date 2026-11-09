"""路径解析：区分"代码位置"与"运行数据位置"。

背景（决策 P1-2）：
    代码在 Windows 的 D:\\xiangmudaima\\zjy14\\，WSL 通过 /mnt/d 访问。
    但 /mnt/d 是跨文件系统挂载，IO 性能差，SQLite 在其上并发写有风险。

因此：
    代码路径      -> 项目目录（可能在 /mnt/d）
    数据/日志/音频 -> WSL 本地 ~/zjy14-data/

可用环境变量覆盖：
    ZJY14_DATA_DIR   数据根目录（默认 ~/zjy14-data）
"""

from __future__ import annotations

import os
from pathlib import Path

#: 项目根目录（本文件在 custom_voice/core/paths.py，上两级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

#: 默认数据根目录。刻意放在 WSL 本地家目录，不放 /mnt/d。
DEFAULT_DATA_DIR = Path.home() / "zjy14-data"


def data_dir() -> Path:
    """数据根目录。"""
    raw = os.environ.get("ZJY14_DATA_DIR", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_DATA_DIR


def db_path() -> Path:
    """SQLite 数据库路径。"""
    return data_dir() / "custom_voice.db"


def log_dir() -> Path:
    """日志目录。"""
    return data_dir() / "logs"


def audio_output_dir() -> Path:
    """合成音频输出目录。"""
    return data_dir() / "audio"


def ensure_dirs() -> None:
    """创建所有运行期目录（幂等）。"""
    for d in (data_dir(), log_dir(), audio_output_dir()):
        d.mkdir(parents=True, exist_ok=True)


#: 参考音频目录（只读，复用 zjy13 的素材）
REFERENCE_AUDIO_DIR = Path("/mnt/d/xiangmudaima/zjy13/server/characters/voices")

#: 模型目录
MODEL_DIR = Path.home() / "models" / "Fun-CosyVoice3-0.5B-2512"


def describe_paths() -> dict[str, str]:
    """供 /api/v1/system/status 展示路径布局。"""
    return {
        "project_root": str(PROJECT_ROOT),
        "data_dir": str(data_dir()),
        "db_path": str(db_path()),
        "log_dir": str(log_dir()),
        "audio_output_dir": str(audio_output_dir()),
        "reference_audio_dir": str(REFERENCE_AUDIO_DIR),
        "model_dir": str(MODEL_DIR),
    }


__all__ = [
    "DEFAULT_DATA_DIR",
    "MODEL_DIR",
    "PROJECT_ROOT",
    "REFERENCE_AUDIO_DIR",
    "audio_output_dir",
    "data_dir",
    "db_path",
    "describe_paths",
    "ensure_dirs",
    "log_dir",
]
