# zjy14 语音链路 · 一键启动控制台（Windows 侧）
#
# 用法（在 zjy14 目录下）：
#   .\scripts\start_launcher.ps1                  # 真模型（cosyvoice3）
#   .\scripts\start_launcher.ps1 -Engine stub     # 轻量 stub 引擎，只验证链路
#   .\scripts\start_launcher.ps1 -Port 50100 -OpenBrowser
#
# 为什么跑在 Windows：
#   ASR 与网关用 zjy13/.venv-asr（Windows），TTS 在 WSL。
#   只有 Windows 侧能同时拉起两边（用 wsl.exe）。
#
# 为什么只用标准库：
#   这是「服务都没起来时」用来救场的工具，不能反过来依赖 FastAPI / torch / node。
#
# 兼容性：只用 PowerShell 5.1 支持的语法（不用 && / ||）。

param(
    [string]$Engine = "cosyvoice3",
    [int]$Port = 50100,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $root "launcher\server.py"

if (-not (Test-Path $launcher)) {
    Write-Host "[错误] 找不到启动器：$launcher" -ForegroundColor Red
    exit 1
}

# Python 选择顺序：
#   1) zjy13/.venv      （Windows 侧已有，含 dotenv 等）
#   2) zjy13/.venv-asr  （ASR 侧，肯定存在）
#   3) 当前 python
$candidates = @(
    (Join-Path $root "..\zjy13\.venv\Scripts\python.exe"),
    (Join-Path $root "..\zjy13\.venv-asr\Scripts\python.exe")
)
$python = $null
foreach ($c in $candidates) {
    if (Test-Path $c) { $python = (Resolve-Path $c).Path; break }
}
if (-not $python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source }
}
if (-not $python) {
    Write-Host "[错误] 找不到 Python（需要 3.10+）" -ForegroundColor Red
    exit 1
}

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  zjy14 语音链路一键启动" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  Python : $python"
Write-Host "  引擎   : $Engine"
Write-Host "  控制台 : http://127.0.0.1:$Port/" -ForegroundColor Green
Write-Host ""
Write-Host "  提示：控制台网页里点「一键启动全部」即可拉起 ASR/LLM/TTS/网关。" -ForegroundColor Yellow
Write-Host "        TTS 使用 zjy14 的 CosyVoice3（:50001）。" -ForegroundColor Yellow
Write-Host "        Ctrl+C 只关闭控制台，不会停止已启动的服务。" -ForegroundColor DarkGray
Write-Host ""

$argsList = @($launcher, "--port", $Port, "--engine", $Engine)
if ($OpenBrowser) { $argsList += "--open-browser" }

& $python @argsList
exit $LASTEXITCODE