"""所有演示入口的角色一致性测试。

背景：``.env`` 里 ``DEFAULT_CHARACTER_ID=elysia``，但四个演示入口中
**三个把角色写死成 default（小满）**：

| 入口 | 修复前 |
|---|---|
| `server/ws/server.py` | ✅ 已修（README 问题 11） |
| `tools/dialogue_loop.py` | ✅ 本来就对 |
| `tools/web_demo.py` | ❌ 硬编码 `default.yaml` + 页面标题写死「小满」 |
| `tools/chat_demo.py` | ❌ `--character` 默认值写死 `"default"` |
| `tools/demo_report.py` | ❌ 同上 |

后果：终端和板子是爱莉希雅，网页/报告却是小满 —— 演示时同一个项目两个角色。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS = PROJECT_ROOT / "tools"


def _src(name: str) -> str:
    return (TOOLS / name).read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# 每个入口都要读 DEFAULT_CHARACTER_ID
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    ["web_demo.py", "chat_demo.py", "demo_report.py", "dialogue_loop.py"],
)
def test_tool_reads_default_character_id(tool: str) -> None:
    src = _src(tool)
    assert "DEFAULT_CHARACTER_ID" in src, (
        f"{tool} 没有读取 DEFAULT_CHARACTER_ID，角色会与 .env 不一致"
    )


@pytest.mark.parametrize(
    "tool",
    ["web_demo.py", "chat_demo.py", "demo_report.py", "dialogue_loop.py"],
)
def test_character_selection_after_env_load(tool: str) -> None:
    """角色选择必须在 load_env_file 之后。"""
    src = _src(tool)
    env_pos = src.index("load_env_file(")
    char_pos = src.index("DEFAULT_CHARACTER_ID")
    assert char_pos > env_pos, f"{tool} 在 .env 加载前就决定了角色"


# ----------------------------------------------------------------------
# 不能硬编码 default
# ----------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["web_demo.py", "chat_demo.py", "demo_report.py"])
def test_no_hardcoded_default_character_arg(tool: str) -> None:
    """argparse 默认值不能是 "default"。"""
    src = _src(tool)
    assert 'add_argument("--character", default="default"' not in src, (
        f"{tool} 的 --character 默认值写死 default（小满）"
    )


def test_web_demo_no_hardcoded_default_yaml() -> None:
    src = _src("web_demo.py")
    assert 'from_file(PROJECT_ROOT / "server" / "characters" / "default.yaml")' not in src


# ----------------------------------------------------------------------
# 页面文案不能写死角色名
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["小满 · 云端 LLM 演示", "跟小满说点什么", "小满 · 网页演示已启动"],
)
def test_web_demo_has_no_hardcoded_xiaoman_text(bad: str) -> None:
    assert bad not in _src("web_demo.py"), f"web_demo.py 仍写死「{bad}」"


def test_web_demo_title_driven_by_api() -> None:
    """前端标题/占位符应据 /api/info 动态渲染。"""
    src = _src("web_demo.py")
    assert "document.title = info.character" in src
    assert "info.character" in src.split("document.title")[1][:200]


# ----------------------------------------------------------------------
# 用到 os.environ 就必须 import os
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool",
    ["web_demo.py", "chat_demo.py", "demo_report.py", "dialogue_loop.py"],
)
def test_os_imported_when_used(tool: str) -> None:
    src = _src(tool)
    if "os.environ" in src:
        assert re.search(r"^import os$", src, re.M), (
            f"{tool} 用了 os.environ 但没 import os"
        )


# ----------------------------------------------------------------------
# 角色卡文件真实存在
# ----------------------------------------------------------------------


def test_env_default_character_card_exists() -> None:
    """`.env` 指向的角色卡必须存在，否则演示启动即失败。"""
    env = PROJECT_ROOT / ".env"
    if not env.is_file():
        pytest.skip("没有 .env（CI 环境）")
    text = env.read_text(encoding="utf-8")
    m = re.search(r"^DEFAULT_CHARACTER_ID=(.+)$", text, re.M)
    assert m, ".env 里没有 DEFAULT_CHARACTER_ID"
    char_id = m.group(1).strip()
    card = PROJECT_ROOT / "server" / "characters" / f"{char_id}.yaml"
    assert card.is_file(), f".env 指向的角色卡 {char_id}.yaml 不存在"
