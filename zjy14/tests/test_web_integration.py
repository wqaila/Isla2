"""网页整合与一键启动的回归测试。

## 背景（2026-11-26）

用户要求两件事：
1. 把独立控制台（:50100 的自包含页面）的**能力整合进 zjy14 主界面**；
2. 给这个网页做**一键启动**。

本文件锁住三件容易退化的事：

- **能力对齐**：主界面那页必须真有「进度面板 / 逐个服务启动 / 日志 /
  LLM 连通性测试」，不能退化成只能点一下的按钮；
- **冷启动前提**：Vite 必须绑 `127.0.0.1`（默认 `localhost` 在本机会解析成
  ::1，导致脚本与文档里写的 `127.0.0.1:5173` 连不上，实测踩过）；
- **脚本编码**：`.ps1` 必须是 UTF-8 **带 BOM**。PowerShell 5.1 对无 BOM 的
  文件按 GBK 解析，中文注释会凑出多余的括号 → 整段脚本解析失败。
  实测 `start_web.ps1` 就因此报 `Unexpected token '}'`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

PAGE = WEB / "src" / "pages" / "ServicesPage.tsx"
VITE = WEB / "vite.config.ts"
STYLE = WEB / "src" / "styles-services.css"
API = WEB / "src" / "api" / "launcher.ts"
START_WEB = SCRIPTS / "start_web.ps1"


def _read(p: Path) -> str:
    assert p.is_file(), f"文件缺失: {p}"
    return p.read_text(encoding="utf-8", errors="replace")


# ======================================================================
# 一、主界面与独立控制台的能力对齐
# ======================================================================


def test_services_page_has_progress_panel() -> None:
    """长任务必须有进度面板（否则「点了没反应」）。"""
    src = _read(PAGE)
    assert "sc-progress" in src, "页面没有进度面板"
    assert "startedAt" in src and "elapsed" in src, "进度面板没有计时"
    assert "sc-progress-fill" in src, "进度条没有填充元素"
    assert _read(STYLE).strip(), "styles-services.css 为空"


def test_services_page_covers_all_operations() -> None:
    """四个操作入口都要在：一键启动、全部停止、单服务启动/停止、日志。"""
    src = _read(PAGE)
    for needle in (
        "launcherApi.startAll",
        "launcherApi.stopAll",
        "launcherApi.start(",
        "launcherApi.stop(",
        "launcherApi.log(",
        "launcherApi.llmTest(",
    ):
        assert needle in src, f"主界面缺少操作：{needle}"


def _strip_ts_comments(src: str) -> str:
    """剥掉 // 与 /* */ 注释（近似实现，够用于本文件的断言）。

    为什么需要：页面的**注释里**会引用历史写法（例如
    「早期写法是 `if (!busy) refresh()`」），直接搜字符串会误报。
    """
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def test_services_page_polls_during_busy() -> None:
    """轮询不能因为 busy 被暂停 —— 否则启动期间卡片不更新。"""
    code = _strip_ts_comments(_read(PAGE))
    assert "polling.current" in code, "缺少轮询防叠加保护"
    assert "if (!busy) refresh()" not in code, (
        "轮询在 busy 期间被暂停了：长任务时卡片不会更新，看起来像卡死"
    )


def test_services_page_reports_all_healthy_case() -> None:
    """全部服务本来就健康时，必须明确说「无需启动」。"""
    src = _read(PAGE)
    assert "allSkipped" in src and "本来就健康" in src


def test_offline_state_gives_actionable_command() -> None:
    """启动器不可达时要给出可复制的命令，而不是只报错。"""
    src = _read(PAGE)
    assert "start_web.ps1" in src, "离线提示里没有 start_web.ps1"
    assert "start_launcher.ps1" in src
    assert "sc-cmd" in src, "命令块样式缺失"


def test_launcher_api_never_throws_when_offline() -> None:
    """启动器离线是常态（冷启动），API 层必须返回 online:false 而不是抛异常。"""
    src = _read(API)
    assert "online: false" in src
    assert "LauncherService" in src


# ======================================================================
# 二、Vite 绑定与代理
# ======================================================================


def test_vite_binds_ipv4_loopback() -> None:
    """必须显式绑 127.0.0.1。

    默认 `host="localhost"` 在本机会解析成 ::1，导致
    `http://127.0.0.1:5173` 连不上（脚本/文档都用这个地址）。实测踩过。
    """
    src = _read(VITE)
    assert 'host: "127.0.0.1"' in src, "vite 没有绑 127.0.0.1"


def test_vite_proxies_launcher_with_rewrite() -> None:
    """`/launcher/*` 必须代理到启动器，并去掉前缀。

    启动器自身路由是 `/api/*`，页面用的是 `/launcher/api/*`。
    另外 `/launcher` 不能被 `/api` 规则吃掉 —— 顺序也要对。
    """
    src = _read(VITE)
    assert '"/launcher"' in src
    assert "rewrite" in src, "缺少 rewrite：前缀不会被去掉"
    i_launcher = src.index('"/launcher"')
    i_api = src.index('"/api"')
    assert i_launcher < i_api or "target: launcher" in src, (
        "代理顺序/目标不明确，/launcher 可能被 /api 规则误吃"
    )


# ======================================================================
# 三、一键启动脚本
# ======================================================================


def test_start_web_script_exists() -> None:
    assert START_WEB.is_file(), "缺少 scripts/start_web.ps1"


@pytest.mark.parametrize("name", ["start_web.ps1", "start_launcher.ps1", "stop_tts.ps1"])
def test_ps1_has_utf8_bom(name: str) -> None:
    """`.ps1` 必须是 UTF-8 **带 BOM**。

    无 BOM 时 PowerShell 5.1 按 ANSI(GBK) 解析 → 中文注释凑出多余括号 →
    `Unexpected token '}'`，整段脚本不执行。
    """
    p = SCRIPTS / name
    assert p.is_file(), f"缺少 {name}"
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), (
        f"{name} 没有 UTF-8 BOM —— PowerShell 5.1 会按 GBK 误读中文注释，"
        "导致解析失败（实测踩过）"
    )


def test_start_web_script_covers_both_services() -> None:
    """脚本要同时管「启动器」和「网页 dev 服务器」。"""
    src = _read(START_WEB)
    assert "launcher\\server.py" in src, "脚本没有启动启动器"
    assert "npm run dev" in src, "脚本没有启动 Vite"
    assert "127.0.0.1:5173" in src or "$webUrl" in src, "脚本没有使用 127.0.0.1"


def test_start_web_script_supports_stop_and_with_services() -> None:
    src = _read(START_WEB)
    assert "[switch]$Stop" in src, "缺少 -Stop"
    assert "[switch]$WithServices" in src, "缺少 -WithServices"
    assert "[switch]$NoBrowser" in src, "缺少 -NoBrowser"


def test_start_web_script_uses_cmd_for_npm() -> None:
    """npm 必须走 cmd：PowerShell 执行策略可能禁止 npm.ps1（本机实测被禁）。"""
    src = _read(START_WEB)
    assert 'cmd.exe' in src or 'cmd /c' in src, (
        "没有通过 cmd 调用 npm —— PowerShell 执行策略会拦截 npm.ps1"
    )


# ======================================================================
# 四、控制台与主界面共用同一套 API（不许各自实现一套启动逻辑）
# ======================================================================


def test_both_uis_hit_same_endpoints() -> None:
    html = (
        Path(__file__).resolve().parent.parent
        / "launcher" / "static" / "index.html"
    )
    standalone = _read(html)
    react = _read(PAGE)
    for ep in ("/api/start-all", "/api/stop-all", "/api/status"):
        assert ep in standalone, f"独立控制台缺少 {ep}"
        # React 侧经 launcher.ts 转发，路径带 /launcher 前缀
        assert ep.split("/api/")[1] in react or ep in _read(API), (
            f"主界面没有对接 {ep}"
        )


def test_service_specs_are_the_single_source_of_truth() -> None:
    """页面不许硬编码服务清单，必须来自 /api/specs 或 /api/status。"""
    src = _read(PAGE)
    assert "services.map" in src, "页面没有按接口返回渲染，疑似硬编码"
    # 端口必须从接口字段渲染，而不是写死一份清单。
    # 注意：页面底部的**说明文字**会提到 :50002 这类端口（那是给人看的文档，
    # 不是数据源），所以这里只断言「渲染用 s.port」。
    assert "s.port" in src, "端口不是取自接口字段 s.port"
    assert "[50001" not in src and "ports = [" not in src, (
        "页面里出现了写死的端口清单"
    )