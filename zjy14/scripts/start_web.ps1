# zjy14 网页一键启动（Windows 侧）
#
# 作用：把「用这个网页」所需的一切一步准备好 ——
#   1) 启动器（:50100，服务编排；只用标准库）
#   2) 前端 dev 服务器（Vite，:5173）
#   3) 打开浏览器到「语音链路启动」页
#   4) （可选）顺便把 ASR/LLM/TTS/网关 也拉起来
#
# 用法（在 zjy14 目录下）：
#   .\scripts\start_web.ps1                     # 起网页 + 启动器，打开页面
#   .\scripts\start_web.ps1 -WithServices       # 再把语音链路也启动
#   .\scripts\start_web.ps1 -Engine stub        # TTS 用轻量 stub（只验链路）
#   .\scripts\start_web.ps1 -NoBrowser          # 不自动开浏览器
#   .\scripts\start_web.ps1 -Stop               # 停止网页与启动器
#
# 为什么网页能「冷启动」：
#   TTS 首次加载模型要十几秒，而一键启动必须在那之前就能点。
#   本页是静态前端（Vite），启动器是独立进程（:50100），
#   两者都不依赖 zjy14 后端 :50001 —— 所以 TTS 没起来也能打开本页。
#
# 兼容性：只用 PowerShell 5.1 支持的语法（不用 && / ||）。

param(
    [string]$Engine = "cosyvoice3",
    [int]$WebPort = 5173,
    [int]$LauncherPort = 50100,
    [switch]$WithServices,
    [switch]$NoBrowser,
    [switch]$Stop
)

$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $PSScriptRoot          # zjy14/
$webDir = Join-Path $root "web"
$launcherPy = Join-Path $root "launcher\server.py"

function Write-Step([string]$msg) { Write-Host ""; Write-Host "=== $msg ===" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "  [OK]   $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host "  [警告] $msg" -ForegroundColor Yellow }
function Write-Err2([string]$msg) { Write-Host "  [失败] $msg" -ForegroundColor Red }
function Write-Note([string]$msg) { Write-Host "         $msg" -ForegroundColor DarkGray }

# netstat 判断端口（不需要管理员权限；Get-NetTCPConnection 在受限环境会拒绝访问）
function Test-PortListening([int]$port) {
    if ($port -le 0) { return $false }
    try {
        $lines = netstat -ano -p TCP 2>$null
        foreach ($l in $lines) {
            if ($l -match "[:.]$port\s+\S+\s+LISTENING") { return $true }
        }
    } catch { }
    return $false
}

function Wait-Http([string]$url, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
            if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# ---------------------------------------------------------------- 停止
if ($Stop) {
    Write-Step "停止网页与启动器"

    # 启动器：按端口找 PID（它可能是任意 Python 启动的）
    if (Test-PortListening $LauncherPort) {
        $lines = netstat -ano -p TCP | Select-String ":$LauncherPort\s+\S+\s+LISTENING"
        foreach ($l in $lines) {
            $pid2 = ($l.Line -split '\s+')[-1]
            if ($pid2 -match '^\d+$') {
                & taskkill /PID $pid2 /T /F | Out-Null
                Write-Ok "已停止启动器（PID $pid2）"
            }
        }
    } else {
        Write-Note "启动器未在运行"
    }

    # Vite：按端口找 PID 后结束进程树（node）
    if (Test-PortListening $WebPort) {
        $lines = netstat -ano -p TCP | Select-String ":$WebPort\s+\S+\s+LISTENING"
        foreach ($l in $lines) {
            $pid2 = ($l.Line -split '\s+')[-1]
            if ($pid2 -match '^\d+$') {
                & taskkill /PID $pid2 /T /F | Out-Null
                Write-Ok "已停止网页 dev 服务器（PID $pid2）"
            }
        }
    } else {
        Write-Note "网页 dev 服务器未在运行"
    }

    Write-Step "完成"
    Write-Note "注意：语音链路服务（TTS/ASR/网关）不会被停止，需要时用页面上的「全部停止」。"
    exit 0
}

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  zjy14 · 网页一键启动" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ---------------------------------------------------------------- 前置检查
Write-Step "检查环境"

if (-not (Test-Path $launcherPy)) {
    Write-Err2 "找不到启动器：$launcherPy"
    exit 1
}
if (-not (Test-Path $webDir)) {
    Write-Err2 "找不到 web 目录：$webDir"
    exit 1
}
if (-not (Test-Path (Join-Path $webDir "node_modules"))) {
    Write-Err2 "web/node_modules 不存在，请先在 web 目录执行：npm install"
    exit 1
}

# Python：优先 zjy13 的两个 venv（Windows 侧已就绪），再退回 PATH
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
    Write-Err2 "找不到 Python（需要 3.10+）"
    exit 1
}
Write-Ok "Python：$python"

# npm 走 cmd（PowerShell 执行策略可能禁止 npm.ps1）
$npmOk = $false
try {
    $v = & cmd /c "npm --version 2>&1"
    if ($LASTEXITCODE -eq 0) { $npmOk = $true; Write-Ok "npm：$($v -join '')" }
} catch { }
if (-not $npmOk) {
    Write-Err2 "npm 不可用（请安装 Node.js 18+）"
    exit 1
}

# ---------------------------------------------------------------- 启动器
Write-Step "启动器（:$LauncherPort）"
if (Test-PortListening $LauncherPort) {
    Write-Ok "已在运行，复用"
} else {
    $logDir = Join-Path $root "logs\launcher"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Start-Process -FilePath $python `
        -ArgumentList @($launcherPy, "--port", $LauncherPort) `
        -WorkingDirectory $root `
        -RedirectStandardOutput (Join-Path $logDir "_server.log") `
        -RedirectStandardError (Join-Path $logDir "_server.err.log") `
        -WindowStyle Hidden | Out-Null
    if (Wait-Http "http://127.0.0.1:$LauncherPort/api/status" 20) {
        Write-Ok "已启动并可用"
    } else {
        Write-Err2 "启动器 20 秒内未就绪，见 logs\launcher\_server.err.log"
        exit 1
    }
}

# ---------------------------------------------------------------- 网页 dev 服务器
Write-Step "网页 dev 服务器（:$WebPort）"
$webUrl = "http://127.0.0.1:$WebPort"
if (Test-PortListening $WebPort) {
    Write-Ok "已在运行，复用"
} else {
    $webLog = Join-Path $root "logs\web_dev.log"
    # 用 cmd 起，避免 PowerShell 执行策略拦截 npm.ps1
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList @("/c", "npm run dev > `"$webLog`" 2>&1") `
        -WorkingDirectory $webDir `
        -WindowStyle Hidden | Out-Null
    if (Wait-Http "$webUrl/" 40) {
        Write-Ok "已启动并可用"
    } else {
        Write-Err2 "网页 dev 服务器 40 秒内未就绪，见 logs\web_dev.log"
        exit 1
    }
}

# ---------------------------------------------------------------- （可选）语音链路
if ($WithServices) {
    Write-Step "启动语音链路（ASR / LLM / TTS / 网关）"
    Write-Note "TTS 首次加载模型可能需要 1~3 分钟…"
    $body = '{"engine":"' + $Engine + '"}'
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$LauncherPort/api/start-all" `
            -Method POST -ContentType "application/json" -Body $body `
            -TimeoutSec 400 -UseBasicParsing -ErrorAction Stop
        $j = $r.Content | ConvertFrom-Json
        foreach ($item in $j.result.results) {
            $mark = if ($item.ok) { "[OK]" } else { "[--]" }
            $color = if ($item.ok) { "Green" } else { "Yellow" }
            Write-Host ("  {0,-5} {1,-9} {2}" -f $mark, $item.name, $item.message) -ForegroundColor $color
        }
        if ($j.result.ok) {
            Write-Ok "语音链路已就绪"
        } else {
            Write-Warn2 "未全部成功（见上面明细，或页面上的进度面板）"
        }
    } catch {
        Write-Warn2 "调用启动器失败：$($_.Exception.Message)"
        Write-Note "可以直接在网页上点「一键启动全部」。"
    }
} else {
    Write-Step "下一步"
    Write-Note "在网页上点「一键启动全部」即可拉起 ASR / LLM / TTS / 网关。"
    Write-Note "（想连这一步也自动化，用 -WithServices）"
}

# ---------------------------------------------------------------- 打开浏览器
$url = "$webUrl/services"
Write-Step "打开页面"
Write-Host "  $url" -ForegroundColor Green
if (-not $NoBrowser) {
    Start-Process $url | Out-Null
}

Write-Host ""
Write-Host "  独立控制台（兜底，不依赖 node）：http://127.0.0.1:$LauncherPort/" -ForegroundColor DarkGray
Write-Host "  停止网页与启动器：.\scripts\start_web.ps1 -Stop" -ForegroundColor DarkGray
exit 0