"""架构约束测试。

强制分层铁律：Web 层不得直接依赖模型实现。

原提示词要求："不要把 Web 层直接耦合到模型内部类；通过 Service 层调用推理引擎。"
这条规则如果只写在文档里，几周后一定会被违反。用测试钉死。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "custom_voice"

#: 禁止出现在 server/ 与 schemas/ 下的 import 前缀
FORBIDDEN_IN_WEB_LAYER = ("cosyvoice", "torch", "onnxruntime", "transformers")

#: 允许接触模型的目录
ENGINE_LAYER_DIRS = ("inference",)


def _iter_py_files(subdir: str) -> list[Path]:
    base = PACKAGE_ROOT / subdir
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*.py") if p.name != "__init__.py")


def _imported_modules(path: Path) -> list[str]:
    """解析文件里的 import 语句，返回模块名列表。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.append(node.module)
    return mods


@pytest.mark.parametrize("subdir", ["server", "schemas"])
def test_web_layer_has_no_model_imports(subdir: str) -> None:
    """server/ 与 schemas/ 不得 import cosyvoice / torch 等模型依赖。"""
    violations: list[str] = []
    for f in _iter_py_files(subdir):
        for mod in _imported_modules(f):
            root = mod.split(".")[0]
            if root in FORBIDDEN_IN_WEB_LAYER:
                violations.append(f"{f.relative_to(PACKAGE_ROOT)} imports {mod}")
    assert not violations, "Web 层直接依赖模型实现：\n" + "\n".join(violations)


def test_routes_only_depend_on_engine_protocol() -> None:
    """routes 不得直接 import 具体引擎实现（只能通过 app.state 注入）。"""
    violations: list[str] = []
    for f in _iter_py_files("server/routes"):
        for mod in _imported_modules(f):
            if "stub_engine" in mod or "cosyvoice_engine" in mod:
                violations.append(f"{f.name} imports {mod}")
    assert not violations, "路由直接依赖具体引擎实现：\n" + "\n".join(violations)


def test_engine_protocol_has_no_heavy_imports() -> None:
    """engine.py 只定义协议，不应 import torch。"""
    mods = _imported_modules(PACKAGE_ROOT / "inference" / "engine.py")
    assert "torch" not in mods
    assert not any(m.startswith("cosyvoice") for m in mods)


def test_core_layer_is_independent() -> None:
    """core/ 不得依赖 inference/ 或 server/。"""
    violations: list[str] = []
    for f in _iter_py_files("core"):
        for mod in _imported_modules(f):
            if mod.startswith("custom_voice.inference") or mod.startswith(
                "custom_voice.server"
            ):
                violations.append(f"{f.name} imports {mod}")
    assert not violations, "core 层反向依赖：\n" + "\n".join(violations)


def test_no_hardcoded_secrets() -> None:
    """源码中不得出现疑似密钥字面量。"""
    suspicious: list[str] = []
    for f in PACKAGE_ROOT.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for token in ("sk-", "Bearer ", "api_key=\"", "api_key='"):
            if token in text:
                # 允许出现字段名，但不允许后接长字面量
                idx = text.find(token)
                snippet = text[idx : idx + 40]
                if token.startswith("sk-") or "Bearer " in snippet:
                    suspicious.append(f"{f.name}: {snippet!r}")
    assert not suspicious, "疑似硬编码密钥：\n" + "\n".join(suspicious)


def test_config_yaml_exists() -> None:
    """默认配置文件必须存在，否则服务启动即失败。"""
    assert (PACKAGE_ROOT.parent / "configs" / "gpu.yaml").is_file()
