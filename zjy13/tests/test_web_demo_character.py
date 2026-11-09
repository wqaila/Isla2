"""网页演示的角色一致性测试。

背景（盘查发现的缺陷，与 README 问题 11 同类但漏在网页端）：
``tools/web_demo.py`` 第 392 行硬编码 ``default.yaml``（小满），
而 ``.env`` 里是 ``DEFAULT_CHARACTER_ID=elysia``。
结果：终端和板子是爱莉希雅，**网页演示却是小满**。
页面标题、输入框占位符、启动横幅也都写死「小满」。

WS 服务端同类问题已修（test_ws_server_voice.py 锁住），网页端此前无测试。
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DEMO = PROJECT_ROOT / "tools" / "web_demo.py"


def _src() -> str:
    return WEB_DEMO.read_text(encoding="utf-8")


def test_character_not_hardcoded_to_default_yaml() -> None:
    """不得硬编码 default.yaml（小满）。"""
    src = _src()
    assert 'from_file(PROJECT_ROOT / "server" / "characters" / "default.yaml")' not in src, (
        "web_demo 硬编码了 default.yaml，与 .env 的 DEFAULT_CHARACTER_ID 冲突"
    )


def test_reads_default_character_id_from_env() -> None:
    src = _src()
    assert "DEFAULT_CHARACTER_ID" in src, (
        "web_demo 没有读取 DEFAULT_CHARACTER_ID，网页角色会与终端/板子不一致"
    )


def test_character_selection_after_env_load() -> None:
    """角色选择必须在 load_env_file 之后，否则读到旧环境变量。"""
    src = _src()
    env_pos = src.index("load_env_file(")
    char_pos = src.index("DEFAULT_CHARACTER_ID")
    assert char_pos > env_pos, "角色选择发生在 .env 加载之前"


def test_page_title_not_hardcoded_character() -> None:
    """页面标题与占位符不能写死角色名。"""
    src = _src()
    assert "小满 · 云端 LLM 演示" not in src, "页面仍写死「小满」"
    assert "跟小满说点什么" not in src, "输入框占位符仍写死「小满」"


def test_page_title_set_from_api_info() -> None:
    """前端应据 /api/info 动态设置标题与占位符。"""
    src = _src()
    assert "document.title = info.character" in src
    assert "placeholder = '跟' + info.character" in src


def test_startup_banner_uses_card_name() -> None:
    """启动横幅应打印实际角色名，而不是写死。"""
    src = _src()
    assert "小满 · 网页演示已启动" not in src
    assert "{card.name} · 网页演示已启动" in src


def test_imports_os_for_env_read() -> None:
    """用到 os.environ 就必须 import os。"""
    src = _src()
    if "os.environ" in src:
        assert "\nimport os\n" in src, "使用了 os.environ 但未 import os"
