# zjy14 · 启动 TTS（WSL 内的 CosyVoice3）
#
# 为什么需要这个脚本：
#   某些环境下 Python 进程无法调用 wsl.exe（Wsl/E_ACCESSDENIED），
#   于是启动器/网页无法把 TTS 拉起来。本脚本是**纯 PowerShell**，
#   不经过 Python，在用户的正常会话里运行，一定可用。
#
# 为什么用「隐藏 powershell 持有 wsl」：
#   TTS 必须有一个常驻的 wsl.exe 会话，否则 WSL2 在会话结束后会关掉 VM，
#   把里面的 uvicorn 一起带走。实测三种写法都会秒退（日志 0 字节）：
#     - `setsid nohup ... &`（脚本立刻返回 -> 会话结束 -> VM 关闭）
#     - `Start-Process wsl.exe -WindowStyle Hidden`
#     - `Start-Process wsl.exe`（不带 Hidden）
#   可行做法：起一个隐藏的 powershell，在它里面**前台**跑 wsl，
#   会话一直活着，TTS 就稳定。持有者 PID 记在 logs\tts_holder.pid。
#
# 用法（在 zjy14 目录下）：
#   .\scripts\start_tts.ps1                # 起 cosyvoice3（真模型）
#   .\scripts\start_tts.ps1 -Engine stub   # 起 stub（只验链路，不加载模型）
#   .\scripts\start_tts.ps1 -Foreground    # 前台运行（能看实时日志，Ctrl+C 退出）
#
# 起好后回到网页点「刷新状态」即可接管 —— 启动器按端口和健康检查判定，
# 不要求 TTS 必须由它自己拉起。
#
# 兼容性：只用 PowerShell 5.1 支持的语法；文件保存为 UTF-8 + BOM。

param(
    [string]$Engine = "cosyvoice3",
    [int]$Port = 50001,
    [switch]$Foreground
)

$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $PSScriptRoot      # zjy14/
$distro = if ($env:ZJY14_WSL_DISTRO) { $env:ZJY14_WSL_DISTRO } else { "Ubuntu-24.04" }
$wslRoot = "/mnt/d/xiangmudaima/zjy14"
$logDir = Join-Path $root "logs"
$logWin = Join-Path $logDir "tts_manual.log"

function Write-Step([string]$msg) { Write-Host ""; Write-Host "=== $msg ===" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "  [OK]   $msg" -ForegroundColor Green }
function Write-Err2([string]$msg) { Write-Host "  [失败] $msg" -ForegroundColor Red }
function Write-Note([string]$msg) { Write-Host "         $msg" -ForegroundColor DarkGray }

function Test-PortListening([int]$port) {
    try {
        $lines = netstat -ano -p TCP 2>$null
        foreach ($l in $lines) {
            if ($l -match "[:.]$port\s+\S+\s+LISTENING") { return $true }
        }
    } catch { }
    return $false
}

function Wait-Health([int]$port, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $h = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3 -ErrorAction Stop
            if ($h.ok) { return $h }
        } catch { }
        Start-Sleep -Milliseconds 800
    }
    return $null
}

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  zjy14 · 启动 TTS（WSL CosyVoice3）" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

Write-Step "检查"
if (Test-PortListening $Port) {
    Write-Ok "端口 $Port 已在监听（TTS 已在运行）"
    $h = Wait-Health $Port 10
    if ($h) {
        # voices 是对象数组（每项有 voice_id），不能直接 -join
        $names = @($h.voices | ForEach-Object { if ($_.voice_id) { $_.voice_id } else { $_ } })
        Write-Note ("引擎={0} 设备={1} 音色={2}" -f $h.engine, $h.device, ($names -join ","))
    }
    Write-Note "回到网页点「刷新状态」即可接管。"
    exit 0
}

$probe = & wsl.exe -d $distro -e bash -lc "echo ZJY14_WSL_OK" 2>&1
if (-not ($probe -match "ZJY14_WSL_OK")) {
    Write-Err2 "WSL 不可用（发行版 $distro）"
    Write-Note "确认已安装：wsl -l -v"
    exit 1
}
Write-Ok "WSL 可用（$distro）"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# 前台模式：直接 exec，日志打在控制台
if ($Foreground) {
    Write-Step "前台启动（Ctrl+C 停止）"
    Write-Note "首次加载模型约 10~20 秒。"
    & wsl.exe -d $distro -e bash -lc "cd $wslRoot && export ZJY14_PORT=$Port && exec bash scripts/start.sh $Engine"
    exit $LASTEXITCODE
}

# 后台模式：在 WSL 内写一个启动脚本，再用 setsid+nohup 脱离终端。
#
# 为什么不直接在命令行里拼：引号层层嵌套极易出错（实测把命令拼坏过）。
# 用 bash heredoc 写入文件，逻辑都在文件里，外层命令保持简单。
# 用 PowerShell 直接把 .sh 写到共享目录（WSL 从 /mnt/d 可见）。
#
# 为什么不用 bash heredoc：多行字符串经 wsl.exe 的参数传递会被截断，
# 实测文件根本没被创建，setsid 启动的是不存在的脚本 → 进程秒退、日志 0 字节。
#
# 必须用 LF 换行：bash 遇到 CRLF 会报 `$'\r': command not found`。
$shWin = Join-Path $logDir "_start_tts.sh"
$shWsl = "$wslRoot/logs/_start_tts.sh"
$shLines = @(
    "#!/usr/bin/env bash",
    "set -e",
    "cd $wslRoot",
    "source ~/cosyvoice-venv/bin/activate",
    "export PYTHONPATH=$wslRoot",
    "export ZJY14_PORT=$Port",
    "exec bash scripts/start.sh $Engine"
)
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($shWin, (($shLines -join "`n") + "`n"), $utf8NoBom)

Write-Step "后台启动"
Write-Note "日志：logs\tts_manual.log"

# 用一个**隐藏的 powershell** 当前台持有者跑 wsl。
#
# 为什么不是 setsid nohup / Start-Process wsl.exe（实测都会失败）：
#   setsid 让脚本立刻返回，WSL2 在最后一个会话结束后会关掉 VM，
#   里面的进程被一起带走 —— 表现为「秒退、日志 0 字节」。
#   Start-Process 直接起 wsl.exe 也同样活不过几秒。
#   powershell 子进程在本环境可靠，而且 wsl 在它里面是前台阻塞运行，
#   会话一直活着，TTS 就不会被关掉。
$holderPidFile = Join-Path $logDir "tts_holder.pid"
$errWin = Join-Path $logDir "tts_manual.err.log"
$holderCmd = "wsl.exe -d $distro -e bash $shWsl"

$holder = Start-Process -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $holderCmd) `
    -WindowStyle Hidden `
    -RedirectStandardOutput $logWin `
    -RedirectStandardError $errWin `
    -PassThru

if ($holder) {
    $holder.Id | Out-File -FilePath $holderPidFile -Encoding ascii
    Write-Ok "后台持有者已启动（PID $($holder.Id)）"
} else {
    Write-Note "持有者启动失败，继续观察端口…"
}

Write-Note "首次加载模型约 10~20 秒，等待就绪…"
$h = Wait-Health $Port 180
if ($h) {
    Write-Ok "TTS 已就绪"
    $names = @($h.voices | ForEach-Object { if ($_.voice_id) { $_.voice_id } else { $_ } })
    Write-Note ("引擎={0} 设备={1} 加载={2}s 音色={3}" -f $h.engine, $h.device, $h.load_s, ($names -join ","))
    Write-Step "下一步"
    Write-Note "回到网页点「刷新状态」，即可看到 TTS 健康并接管。"
    exit 0
}

Write-Err2 "180 秒内未就绪"
if (Test-Path $logWin) {
    Write-Note "日志尾部："
    Get-Content $logWin -Tail 12 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }
}
exit 1