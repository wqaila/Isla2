"""一键启动脚本的一致性测试。

背景：``scripts/start_all.ps1`` 把 TTS（WSL2）与 WS 服务端整合成一条命令。
这类「胶水脚本」最容易出现的问题，都是我在写它时真实踩到的：

1. **参数名与文档不一致**：注释里写 ``-HostAddr``，param 里是 ``$BindHost``，
   导致传参静默失效（PowerShell 不会报未知参数，若用了位置参数）。
2. **无 BOM 的 UTF-8 中文脚本被按 GBK 解码**：PowerShell 5.1 的 ParseFile
   会把中文注释解坏，报出与真实位置无关的语法错误
   （实测报「意外的标记 '}'」在第 177 行，真因在第 40 行的中文注释）。
3. **依赖 Get-NetTCPConnection / Get-NetIPAddress**：这两个 cmdlet 在受限
   权限下「拒绝访问」，会把正在运行的服务误判成没启动（实测踩到）。
4. **.bat 与 .ps1 参数名漂移**：双击入口传错参数，功能静默降级。

本文件把这些都锁住。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PS1 = PROJECT_ROOT / "scripts" / "start_all.ps1"
BAT = PROJECT_ROOT / "一键启动.bat"


def _ps1_text() -> str:
    # utf-8-sig 自动吃掉 BOM
    return PS1.read_text(encoding="utf-8-sig")


# ----------------------------------------------------------------------
# 文件存在与编码
# ----------------------------------------------------------------------


def test_script_exists() -> None:
    assert PS1.is_file(), "缺少 scripts/start_all.ps1"


def test_bat_exists() -> None:
    assert BAT.is_file(), "缺少 一键启动.bat"


def test_ps1_has_utf8_bom() -> None:
    """含中文的 .ps1 必须有 BOM。

    没有 BOM 时 Windows PowerShell 5.1 按系统 ANSI(GBK) 解码，
    中文注释会破坏语法分析，报出与真实原因无关的错误。
    """
    raw = PS1.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), (
        "start_all.ps1 缺少 UTF-8 BOM；PowerShell 5.1 会按 GBK 解码中文注释导致语法错误"
    )


def test_ps1_decodes_as_utf8() -> None:
    raw = PS1.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    raw.decode("utf-8")  # 失败即抛错


# ----------------------------------------------------------------------
# .bat 必须纯 ASCII
# ----------------------------------------------------------------------


def test_bat_is_pure_ascii() -> None:
    """`.bat` 里不能出现非 ASCII 字节。

    这是实测踩到的坑：Windows cmd 按 OEM 代码页（中文系统是 GBK/936）读取
    .bat，而文件是 UTF-8。UTF-8 的中文首字节 + 换行符在 GBK 下会被当成
    「前导字节 + 尾字节」配对，**把换行吃掉**，导致多行合并、命令被截断。

    当时的报错：
        'e' 不是内部或外部命令
        'rt_all.ps1" -BindHost 0.0.0.0' 不是内部或外部命令

    即 ``-File scripts\\start_all.ps1`` 被从中间劈开。
    给 .bat 加 BOM 也不行（cmd 会把 BOM 当命令的一部分）。
    唯一稳妥做法：.bat 只用 ASCII，中文提示全部交给带 BOM 的 .ps1。
    """
    raw = BAT.read_bytes()
    bad = [(i, b) for i, b in enumerate(raw) if b > 127]
    assert not bad, (
        f"一键启动.bat 含 {len(bad)} 个非 ASCII 字节（首个在偏移 {bad[0][0]}）；"
        "cmd 按 GBK 读取 UTF-8 会吃掉换行、截断命令。请改为纯 ASCII，"
        "中文提示放到 start_all.ps1（UTF-8 with BOM）里。"
    )


def test_bat_has_no_utf8_bom() -> None:
    """`.bat` 不能有 BOM —— cmd 会把 BOM 字节当成命令的一部分。"""
    raw = BAT.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), (
        "一键启动.bat 带了 UTF-8 BOM，cmd 会把它当命令的一部分执行"
    )


def test_other_bats_are_pure_ascii() -> None:
    """其它双击入口同样要纯 ASCII。"""
    others = ["启动演示.bat", "开始对话.bat"]
    for name in others:
        p = PROJECT_ROOT / name
        if not p.is_file():
            continue
        raw = p.read_bytes()
        bad = [b for b in raw if b > 127]
        assert not bad, f"{name} 含非 ASCII 字节，cmd 下会误读"


def test_bat_uses_crlf_or_lf_consistently() -> None:
    """.bat 换行要一致，混用会让 cmd 解析异常。"""
    raw = BAT.read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    assert not (crlf and lf), (
        f"一键启动.bat 混用换行符（CRLF={crlf}, 裸LF={lf}），cmd 可能解析异常"
    )


# ----------------------------------------------------------------------
# 参数名一致性
# ----------------------------------------------------------------------


def _declared_params() -> list[str]:
    text = _ps1_text()
    i = text.index("param(")
    j = text.index(")", i)
    block = text[i:j]
    return re.findall(r"\$(\w+)", block)


def test_expected_params_declared() -> None:
    params = _declared_params()
    for name in ("BindHost", "WSPort", "TTSPort", "TTSTimeout",
                 "SkipTTS", "SkipWS", "Status", "Stop"):
        assert name in params, f"param 块缺少 ${name}"


def test_doc_usage_matches_declared_params() -> None:
    """头部注释里 ``start_all.ps1 -Xxx`` 的写法必须是真实参数名。

    踩过的坑：注释写 -HostAddr，param 是 $BindHost，传参静默不生效。
    只匹配「脚本名后面跟的参数」，避免把函数名、PowerShell 内置参数算进来。
    """
    text = _ps1_text()
    head = text[:1500]
    declared = {p.lower() for p in _declared_params()}
    # 只找 start_all.ps1 后面紧跟的 -Xxx
    used = set(re.findall(r"start_all\.ps1\s+-([A-Z]\w+)", head))
    assert used, "头部注释里没有任何示例用法"
    unknown = [u for u in used if u.lower() not in declared]
    assert not unknown, (
        f"注释里用了未声明的参数 {unknown}；已声明 {sorted(declared)}"
    )


def test_bat_uses_declared_params() -> None:
    """双击入口传给 start_all.ps1 的参数必须真实存在。"""
    bat = BAT.read_text(encoding="utf-8", errors="replace")
    declared = {p.lower() for p in _declared_params()}
    # 只匹配 start_all.ps1 后面的参数；powershell 自身的 -File/-NoProfile 不算
    used = set(re.findall(r"start_all\.ps1\"?\s+-([A-Z]\w+)", bat))
    unknown = [u for u in used if u.lower() not in declared]
    assert not unknown, f"一键启动.bat 传了未声明的参数 {unknown}"


# ----------------------------------------------------------------------
# 不要依赖受限的 cmdlet
# ----------------------------------------------------------------------


@pytest.mark.parametrize("cmdlet", ["Get-NetTCPConnection", "Get-NetIPAddress"])
def test_does_not_use_restricted_net_cmdlets(cmdlet: str) -> None:
    """这两个 cmdlet 在受限权限下会「拒绝访问」，导致误判服务未启动。"""
    text = _ps1_text()
    # 允许出现在注释里（解释为什么不使用），但不能出现在实际调用中
    code_lines = [
        ln for ln in text.splitlines()
        if not ln.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    assert cmdlet not in code, (
        f"代码里仍调用 {cmdlet}；它在受限权限下会失败，请改用 netstat"
    )


def test_uses_netstat_based_probe() -> None:
    """端口探测应基于 netstat（普通用户权限即可）。"""
    text = _ps1_text()
    assert "netstat" in text
    assert "Test-PortListening" in text


# ----------------------------------------------------------------------
# 关键行为存在
# ----------------------------------------------------------------------


def test_has_stop_switch() -> None:
    text = _ps1_text()
    assert "$Stop" in text
    assert "Stop-Process" in text


def test_has_status_switch() -> None:
    text = _ps1_text()
    assert "$Status" in text


def test_waits_for_tts_health() -> None:
    """必须轮询 /health，而不是启动后立刻起 WS。

    WS 服务启动时会做 TTS 健康检查，TTS 没起来会直接退出码 2。
    """
    text = _ps1_text()
    assert "/health" in text
    assert "TTSTimeout" in text


def test_checks_ws_port_before_reporting_success() -> None:
    text = _ps1_text()
    assert "Test-PortListening $WSPort" in text


def test_checks_proxy_from_env() -> None:
    """代理未启动时 LLM 会失败，脚本应提前提示。"""
    text = _ps1_text()
    assert "LLM_PROXY" in text


def test_tts_not_ready_blocks_ws_start() -> None:
    """TTS 没就绪时应拒绝启动 WS，并说明原因（不静默降级）。"""
    text = _ps1_text()
    assert "TTS 未就绪，跳过 WS 启动" in text
    assert "exit 2" in text


# ----------------------------------------------------------------------
# 语法可解析（尽力而为，缺 powershell 时跳过）
# ----------------------------------------------------------------------


def test_powershell_parser_accepts_script() -> None:
    import shutil
    import subprocess

    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        pytest.skip("没有 PowerShell，跳过语法检查")

    script = (
        f"$e=$null;"
        f"[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{PS1}',[ref]$null,[ref]$e)|Out-Null;"
        f"if($e){{$e.Count}}else{{0}}"
    )
    r = subprocess.run(
        [ps, "-NoProfile", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (r.stdout or "").strip()
    assert out == "0", f"PowerShell 解析报错 {out} 个：{r.stderr[:300]}"
