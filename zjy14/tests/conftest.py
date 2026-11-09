"""pytest 共享 fixture。

原则：
- 每个测试用独立临时目录，不污染 ~/zjy14-data
- 不加载真模型，全部走 StubEngine
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录。"""
    monkeypatch.setenv("ZJY14_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def gpu_config(tmp_data_dir: Path):  # type: ignore[no-untyped-def]
    """默认测试配置：stub 引擎、cpu、跳过显存检查。"""
    from custom_voice.config.loader import load_config

    return load_config(
        overrides={
            "engine": "stub",
            "device": "cpu",
            "memory_safety_margin_mb": 0,
        }
    )


@pytest.fixture()
def app(gpu_config):  # type: ignore[no-untyped-def]
    """构造测试用 FastAPI 应用（不启动 uvicorn）。"""
    from custom_voice.server.app import build_app

    return build_app(gpu_config)


@pytest.fixture()
def client(app):  # type: ignore[no-untyped-def]
    """TestClient。with 语句触发 lifespan。"""
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c
