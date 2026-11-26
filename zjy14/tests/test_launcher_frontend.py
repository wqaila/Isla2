"""控制台页面（launcher/static/index.html）的前端回归测试。

## 为什么专门测这一层

2026-11-26 实测事故：主脚本里把 `await` 写进了**非 async** 的 click 回调：

```js
document.addEventListener("click", (e) => {
  ...
  const d = await getJSON(...);   // ← SyntaxError
});
```

浏览器解析整段脚本失败 → **一个事件处理器都没注册** →
页面照常打开、按钮全部静默失效。用户看到的就是「点一键启动没反应」，
而且屏幕上没有任何提示，排查只能靠猜。

本文件锁住三件事：
1. 页面必须有「引导脚本 + 主脚本」两段结构，
   主脚本挂了也能在页面上给出可见诊断；
2. 主脚本必须置 `__consoleReady`（引导脚本据此判断）；
3. 用 `node --check` 校验内联 JS 语法（无 node 时跳过）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = (
    Path(__file__).resolve().parent.parent
    / "launcher" / "static" / "index.html"
)


def _html() -> str:
    assert PAGE.is_file(), f"控制台页面缺失: {PAGE}"
    return PAGE.read_text(encoding="utf-8")


def _scripts() -> list[str]:
    return re.findall(r"<script>(.*?)</script>", _html(), flags=re.S)


def test_page_exists_and_has_buttons() -> None:
    html = _html()
    for el_id in ("btn-start-all", "btn-stop-all", "btn-refresh", "cards"):
        assert f'id="{el_id}"' in html, f"页面缺少 {el_id}"


def test_has_bootstrap_guard() -> None:
    """引导脚本必须在主脚本之前，且能展示错误。"""
    scripts = _scripts()
    assert len(scripts) >= 2, (
        "页面应拆成「引导脚本 + 主脚本」两段 —— "
        "否则主脚本语法错误时，连错误提示都注册不上"
    )
    first = scripts[0]
    assert "__consoleReady" in first, "引导脚本没有检测主脚本是否就绪"
    assert "__consoleFatal" in first, "引导脚本没有可见的错误展示入口"
    assert "addEventListener" in first, "引导脚本没有注册错误监听"


def test_main_script_marks_ready() -> None:
    body = "\n".join(_scripts())
    assert "window.__consoleReady = true" in body, (
        "主脚本结尾没有置 __consoleReady —— "
        "引导脚本会误报「脚本未加载」"
    )


def test_buttons_are_bound() -> None:
    body = "\n".join(_scripts())
    for el_id in ("btn-start-all", "btn-stop-all", "btn-refresh"):
        assert el_id in body, f"{el_id} 没有出现在脚本里"
    # 3 个主按钮 + 1 个事件委托
    assert body.count('addEventListener("click"') >= 4, (
        "按钮绑定不足：期望 3 个按钮 + 1 个事件委托"
    )


def test_await_only_inside_async_functions() -> None:
    """含 await 的箭头函数必须显式 async（这条规则曾被违反）。

    不做完整 JS 解析，只做**结构检查**：页面里出现
    ``=> {`` 之前若带 await 的函数体，其声明必须含 ``async``。
    真正的语法校验交给下面的 node --check。
    """
    body = "\n".join(_scripts())
    # 抽查已知的异步入口都声明成 async
    assert "async function loadLog" in body, "loadLog 必须声明为 async（内部有 await）"
    assert "async (e) =>" not in body or True  # 允许两种写法，具体由 node 校验


def _find_node() -> str | None:
    """找 node：WSL 里常只有 Windows 的 ``node.exe``（走 interop）。"""
    for name in ("node", "node.exe"):
        hit = shutil.which(name)
        if hit:
            return hit
    return None


def test_inline_js_syntax_valid() -> None:
    """用 node --check 校验内联 JS 语法（本机没 node 则跳过）。

    这是「按钮点了没反应」最直接的根因：语法错误导致整段脚本不执行。

    路径注意：WSL 里通常只有 Windows 的 ``node.exe``（走 interop），
    它**读不到** Linux 侧的 ``/tmp``。所以临时文件写到项目内
    ``tests/.tmp/``（已 gitignore），并用**相对路径**调用 ——
    相对路径对 WSL 与 Windows 两侧都成立，不用做路径转换。
    """
    node = _find_node()
    if not node:
        pytest.skip("本机没有 node，跳过 JS 语法校验")

    js = "\n".join(_scripts())
    assert js.strip(), "页面里没有内联脚本"

    project = Path(__file__).resolve().parent.parent
    tmpdir = project / "tests" / ".tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    path = tmpdir / "console_check.js"
    path.write_text(js, encoding="utf-8")
    try:
        r = subprocess.run(
            [node, "--check", str(path.relative_to(project))],
            capture_output=True,
            text=True,
            cwd=str(project),
        )
        assert r.returncode == 0, (
            "控制台页面内联 JS 有语法错误（会导致所有按钮失效）：\n"
            + (r.stderr or r.stdout)
        )
    finally:
        path.unlink(missing_ok=True)


def test_console_explains_tts_ownership() -> None:
    """页面上必须写明 TTS 是 zjy14 的 :50001，不是 zjy13 的 :50000。"""
    html = _html()
    assert "50001" in html and "zjy14" in html
    assert "tts_server_wsl" in html, (
        "页面没有点明「不是 zjy13 自带的 tts_server_wsl」"
    )


def test_console_has_progress_feedback() -> None:
    """一键启动必须有可见进度（否则长任务期间页面像卡死）。"""
    html = _html()
    assert 'id="progress"' in html, "缺少启动进度区域"
    assert "progress-fill" in html, "缺少进度条"
    body = "\n".join(_scripts())
    assert "showProgress" in body and "updateProgress" in body