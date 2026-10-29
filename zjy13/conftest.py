"""pytest 配置：把项目根目录加入 sys.path，并在缺依赖时跳过对应测试。

历史上测试被拆在两个 venv 里跑（``.venv`` 跑 LLM/TTS，``.venv-asr`` 跑 ASR），
直接 ``pytest tests`` 会在收集阶段因缺 numpy 而整体中断。现在 ``.venv``
补齐了 numpy/miniaudio，但 ASR 引擎测试仍需要 torch（只有 ``.venv-asr`` 有）。
这里用 collect_ignore 精确跳过，而不是让整个测试会话崩掉。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _has_module(name: str) -> bool:
    try:
        __import__(name)
    except Exception:  # noqa: BLE001
        return False
    return True


# ASR 引擎测试依赖 torch，只在 .venv-asr 里跑
collect_ignore: list[str] = []
if not _has_module("torch"):
    collect_ignore.append("tests/test_asr_engine.py")


def pytest_report_header(config: pytest.Config) -> str:
    return (
        f"zjy13: torch={'yes' if _has_module('torch') else 'no'}, "
        f"numpy={'yes' if _has_module('numpy') else 'no'}"
    )
