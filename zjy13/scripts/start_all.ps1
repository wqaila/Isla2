# 一键启动：TTS（WSL2）+ WebSocket 服务端（Windows）
#
# 用法：
#   .\scripts\start_all.ps1                     # 本机演示（127.0.0.1）
#   .\scripts\start_all.ps1 -BindHost 0.0.0.0   # 让板子能连（热点/局域网）
#   .\scripts\start_all.ps1 -SkipTTS            # 只起 WS（TTS 已在跑）
#   .\scripts\start_all.ps1 -SkipWS             # 只起 TTS
#   .\scripts\start_all.ps1 -TTSTimeout 300     # 首次下载模型时放宽等待
#   .\scripts\start_all.ps1 -Stop               # 停止两个服务
#
# 为什么需要它：
#   原本要手工开两个终端、按顺序启动、还要自己判断 TTS 有没有加载完。
#   顺序错了（WS 先起）会直接退出码 2 —— 因为 WS 启动时会做 TTS 健康检查。

param(
    [string]$BindHost = "127.0.0.1",
    [int]$WSPort = 8765,
    [int]$TTSPort = 50000,
    [int]$TTSTimeout = 120,
    [switch]$SkipTTS,
    [switch]$SkipWS,
    [switch]$NoHotspotCheck,
    [switch]$Status,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$pyWin = Join-Path $root ".venv\Scripts\python.exe"
$pyAsr = Join-Path $root ".venv-asr\Scripts\python.exe"
$serveSh = "/mnt/d/xiangmudaima/zjy13/scripts/serve_tts_wsl.sh"

function Write-Step([string]$msg) { Write-Host ""; Write-Host "=== ${msg} ===" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "  [OK]   ${msg}" -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host "  [警告] ${msg}" -ForegroundColor Yellow }
function Write-Err2([string]$msg) { Write-Host "  [失败] ${msg}" -ForegroundColor Red }
function Write-Note([string]$msg) { Write-Host "         ${msg}" -ForegroundColor DarkGray }

# 端口是否在监听。
# 不直接用 Get-NetTCPConnection：该 cmdlet 在受限权限/沙箱下会「拒绝访问」，
# 会把正在运行的服务误判成没启动（实测踩到过）。netstat 只需普通用户权限。
function Test-PortListening([int]$port) {
    if ($port -le 0) { return $false }
    try {
        $lines = netstat -ano -p TCP 2>$null
        foreach ($l in $lines) {
            # 形如：  TCP    127.0.0.1:7897    0.0.0.0:0    LISTENING    25568
            if ($l -match "[:.]${port}\s+\S+\s+LISTENING") { return $true }
        }
    } catch { }
    return $false
}

# 本机是否持有某个地址（不依赖 Get-NetIPAddress）
function Test-LocalAddress([string]$ip) {
    try {
        $lines = netstat -ano -p TCP 2>$null
        foreach ($l in $lines) {
            if ($l -match [regex]::Escape($ip)) { return $true }
        }
    } catch { }
    return $false
}

# ------------------------------------------------------------------ 状态
if ($Status) {
    Write-Step "当前状态"
    $ttsUp = $false
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:${TTSPort}/health" -TimeoutSec 4 -ErrorAction Stop
        $ttsUp = $true
        $info = $r.Content | ConvertFrom-Json
        $voiceNames = ($info.voices | ForEach-Object { $_.voice_id }) -join ", "
        Write-Ok "TTS 运行中（端口 ${TTSPort}）"
        Write-Note "设备=$($info.device)  采样率=$($info.sample_rate)  音色=[${voiceNames}]"
    } catch {
        Write-Warn2 "TTS 未运行（端口 ${TTSPort}）"
    }

    if (Test-PortListening $WSPort) {
        Write-Ok "WS 服务运行中（端口 ${WSPort}）"
    } else {
        Write-Warn2 "WS 服务未运行（端口 ${WSPort}）"
    }

    $proxyLine2 = Select-String -Path (Join-Path $root ".env") -Pattern "^\s*LLM_PROXY\s*=\s*(\S+)" -ErrorAction SilentlyContinue
    if ($proxyLine2) {
        $pu = $proxyLine2.Matches[0].Groups[1].Value
        if ($pu -and $pu -match ':(\d+)\s*$') {
            if (Test-PortListening ([int]$Matches[1])) {
                Write-Ok "代理 ${pu} 运行中"
            } else {
                Write-Warn2 "代理 ${pu} 未运行（LLM 调用会失败）"
            }
        }
    }
    Write-Host ""
    exit 0
}

# ------------------------------------------------------------------ 停止
if ($Stop) {
    Write-Step "停止服务"
    $targets = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and (
                $_.CommandLine -like "*server.ws.server*" -or
                $_.CommandLine -like "*tts_server_wsl*"
            )
        }
    if (-not $targets) {
        Write-Warn2 "没有找到运行中的 TTS / WS 进程"
    } else {
        foreach ($p in $targets) {
            Write-Host "  停止 PID=$($p.ProcessId)" -ForegroundColor Gray
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Write-Ok "已停止 $($targets.Count) 个进程"
    }
    Write-Note "WSL 内的 TTS 也可直接关掉它所在的窗口。"
    exit 0
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  AI-Role 一键启动" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ------------------------------------------------------------ 前置检查
Write-Step "0. 前置检查"

if (-not (Test-Path $pyWin)) {
    Write-Err2 "找不到 .venv：$pyWin"
    Write-Note "请先创建虚拟环境：python -m venv .venv"
    exit 1
}
if (-not (Test-Path $pyAsr)) {
    Write-Warn2 "找不到 .venv-asr，WS 服务将改用 .venv（可能缺 torch/numpy）"
    $pyAsr = $pyWin
}
if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Err2 "找不到 .env，LLM 无法调用"
    Write-Note "请复制 .env.example 为 .env 并填写 LLM_API_KEY"
    exit 1
}
Write-Ok "Python 环境与 .env 就绪"

# Clash：.env 里配了代理就必须能连上，否则 LLM 调用会失败
$envFile = Join-Path $root ".env"
$proxyLine = Select-String -Path $envFile -Pattern "^\s*LLM_PROXY\s*=\s*(\S+)" -ErrorAction SilentlyContinue
if ($proxyLine) {
    $proxyUrl = $proxyLine.Matches[0].Groups[1].Value
    if ($proxyUrl) {
        $proxyPort = 0
        if ($proxyUrl -match ':(\d+)\s*$') { $proxyPort = [int]$Matches[1] }
        $listening = $false
        if ($proxyPort -gt 0) {
            $listening = Test-PortListening $proxyPort
        }
        if ($listening) {
            Write-Ok "代理 ${proxyUrl} 正在监听"
        } else {
            Write-Warn2 "代理 ${proxyUrl} 未监听 —— LLM 调用会失败"
            Write-Note "请启动 Clash Verge，或把 .env 的 LLM_PROXY 留空（若可直连）"
        }
    } else {
        Write-Ok "未配置代理（直连）"
    }
} else {
    Write-Ok "未配置代理（直连）"
}

# 热点与板子（仅提示，不阻断）
if ((-not $NoHotspotCheck) -and ($BindHost -eq "0.0.0.0")) {
    $hotspot = Test-LocalAddress "192.168.137.1"
    if ($hotspot) {
        Write-Ok "移动热点已开启（192.168.137.1）"
        # Test-Connection 在受限环境下会 Access denied。三态处理：
        #   $null = 探测不可用（不报“不在线”，避免误导）
        #   $true / $false = 真实结果
        $boardUp = $null
        try {
            $boardUp = Test-Connection 192.168.137.174 -Count 1 -Quiet -ErrorAction Stop
        } catch {
            $boardUp = $null
        }
        if ($boardUp -eq $true) {
            Write-Ok "板子 192.168.137.174 在线"
        } elseif ($boardUp -eq $false) {
            Write-Warn2 "板子不在线（192.168.137.174 无响应），可稍后上电"
        } else {
            Write-Note "无法探测板子（当前权限受限），跳过在线检查"
        }
    } else {
        Write-Warn2 "移动热点未开启，板子无法连接"
        Write-Note "板子需连 2.4GHz 热点；不开热点只能做纯软件演示"
    }
}

# ---------------------------------------------------------------- TTS
$ttsReady = $false
if ($SkipTTS) {
    Write-Step "1. 跳过 TTS（-SkipTTS）"
    $ttsReady = $true
} else {
    Write-Step "1. 启动 TTS 服务（WSL2）"

    $already = $false
    $r = $null
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:${TTSPort}/health" -TimeoutSec 3 -ErrorAction Stop
        $already = $true
        Write-Ok "TTS 已在运行"
    } catch {
        Write-Note "正在 WSL2 内启动（首次会加载模型，约 15~20 秒）"
    }

    if ($already) {
        $ttsReady = $true
        try {
            $info = $r.Content | ConvertFrom-Json
            $voiceNames = ($info.voices | ForEach-Object { $_.voice_id }) -join ", "
            Write-Note "设备=$($info.device)  采样率=$($info.sample_rate)  音色=[${voiceNames}]"
        } catch { }
    } else {
        $wslCmd = "wsl -e bash ${serveSh}"
        Start-Process -FilePath "powershell" `
            -ArgumentList "-NoExit", "-Command", $wslCmd `
            -WorkingDirectory $root | Out-Null

        Write-Note "等待 TTS 就绪（最多 ${TTSTimeout} 秒）"
        $deadline = (Get-Date).AddSeconds($TTSTimeout)
        while ((Get-Date) -lt $deadline) {
            try {
                $r = Invoke-WebRequest "http://127.0.0.1:${TTSPort}/health" -TimeoutSec 4 -ErrorAction Stop
                $ttsReady = $true
                break
            } catch {
                Start-Sleep -Seconds 3
                Write-Host "." -NoNewline -ForegroundColor DarkGray
            }
        }
        Write-Host ""

        if ($ttsReady) {
            Write-Ok "TTS 就绪"
            try {
                $info = $r.Content | ConvertFrom-Json
                $voiceNames = ($info.voices | ForEach-Object { $_.voice_id }) -join ", "
                Write-Note "设备=$($info.device)  采样率=$($info.sample_rate)  音色=[${voiceNames}]"
            } catch { }
        } else {
            Write-Err2 "TTS 在 ${TTSTimeout} 秒内未就绪"
            Write-Note "看那个新开的窗口里的日志。常见原因："
            Write-Note "  - 首次启动正在下载 wetext 模型（网络慢），用 -TTSTimeout 300 重试"
            Write-Note "  - WSL 内 ~/cosyvoice-venv 不存在，需先跑 scripts/setup_cosyvoice_wsl.sh"
        }
    }
}

# ----------------------------------------------------------------- WS
if ($SkipWS) {
    Write-Step "2. 跳过 WS（-SkipWS）"
} else {
    Write-Step "2. 启动 WebSocket 服务端"

    $wsListening = Test-PortListening $WSPort
    if ($wsListening) {
        Write-Ok "WS 服务已在运行（端口 ${WSPort}）"
    } else {
        if (-not $ttsReady) {
            Write-Err2 "TTS 未就绪，跳过 WS 启动"
            Write-Note "WS 启动时必须能连上 TTS，否则以退出码 2 结束（设计如此，不静默降级）"
            exit 2
        }

        $inner = "cd '${root}'; `$env:PYTHONUTF8='1'; & '${pyAsr}' -m server.ws.server --host ${BindHost} --port ${WSPort}"
        Start-Process -FilePath "powershell" `
            -ArgumentList "-NoExit", "-Command", $inner `
            -WorkingDirectory $root | Out-Null

        Write-Note "等待端口 ${WSPort} 监听..."
        $deadline = (Get-Date).AddSeconds(60)
        $ok = $false
        while ((Get-Date) -lt $deadline) {
            if (Test-PortListening $WSPort) {
                $ok = $true
                break
            }
            Start-Sleep -Seconds 2
            Write-Host "." -NoNewline -ForegroundColor DarkGray
        }
        Write-Host ""

        if ($ok) {
            Write-Ok "WS 服务已监听 ws://${BindHost}:${WSPort}"
        } else {
            Write-Err2 "WS 服务在 60 秒内未监听，请看新开的窗口日志"
            exit 3
        }
    }
}

# ---------------------------------------------------------------- 汇总
Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  启动完成" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  TTS      : http://127.0.0.1:${TTSPort}/health"
Write-Host "  WS 服务  : ws://${BindHost}:${WSPort}"
if ($BindHost -eq "0.0.0.0") {
    Write-Host "  板子连接 : 192.168.137.1 端口 ${WSPort}（开机自动连）"
}
Write-Host ""
Write-Host "  纯软件演示（另开窗口）：" -ForegroundColor DarkGray
Write-Host "    .\启动演示.bat        网页对话" -ForegroundColor DarkGray
Write-Host "    .\开始对话.bat        终端对话" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  停止服务：" -ForegroundColor DarkGray
Write-Host "    .\scripts\start_all.ps1 -Stop" -ForegroundColor DarkGray
Write-Host "    或直接关掉那两个服务窗口" -ForegroundColor DarkGray
Write-Host "==============================================" -ForegroundColor Green
