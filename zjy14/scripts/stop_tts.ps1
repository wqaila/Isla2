# 仅停止 zjy14 的 TTS（WSL 内的 uvicorn）
#
# 用法（在 zjy14 目录下）：
#   .\scripts\stop_tts.ps1
#
# 为什么单独一个脚本：
#   某些环境下启动器（Python 进程）无法访问 WSL，`wsl.exe` 返回
#   Wsl/E_ACCESSDENIED。而 WSL 里的 uvicorn 又不会随 Windows 侧的 taskkill
#   退出（Windows 上看到的监听 PID 其实是 WSL 的端口转发进程 wslrelay，
#   杀掉它既停不掉 TTS，还会破坏 WSL 转发）。
#   所以 WSL 侧服务必须从 WSL 内部停 —— 本脚本用纯 PowerShell 做这件事，
#   不经过 Python，在用户自己的会话里一定可用。
#
# 兼容性：只用 PowerShell 5.1 支持的语法；文件保存为 UTF-8 + BOM。

$ErrorActionPreference = "Continue"

$distro = if ($env:ZJY14_WSL_DISTRO) { $env:ZJY14_WSL_DISTRO } else { "Ubuntu-24.04" }
$root = Split-Path -Parent $PSScriptRoot
$port = 50001
$holderPidFile = Join-Path $root "logs\tts_holder.pid"

function Test-PortListening([int]$p) {
    try {
        $lines = netstat -ano -p TCP 2>$null
        foreach ($l in $lines) {
            if ($l -match "[:.]$p\s+\S+\s+LISTENING") { return $true }
        }
    } catch { }
    return $false
}

Write-Host "=== 停止 zjy14 TTS（WSL: $distro）===" -ForegroundColor Cyan

# 1) 在 WSL 内 pkill（找到时退出码 0，没找到为 1）
& wsl.exe -d $distro -e bash -lc "pkill -f 'custom_voice.server' && echo KILLED || echo NONE"

# 2) 清理「持有者」进程（start_tts.ps1 起的隐藏 powershell）。
#    它只是把 wsl 当子进程跑，wsl 退出后它一般会自己结束；
#    这里兜底清理，避免留下僵尸。
if (Test-Path $holderPidFile) {
    $holderPid = (Get-Content $holderPidFile -Raw).Trim()
    if ($holderPid -match '^\d+$') {
        $hp = Get-Process -Id $holderPid -ErrorAction SilentlyContinue
        if ($hp) {
            Stop-Process -Id $holderPid -Force -ErrorAction SilentlyContinue
            Write-Host "  已清理持有者进程（PID $holderPid）" -ForegroundColor DarkGray
        }
    }
    Remove-Item $holderPidFile -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2

# 3) 校验端口
if (Test-PortListening $port) {
    Write-Host "[警告] 端口 $port 仍在监听：" -ForegroundColor Yellow
    netstat -ano -p TCP | Select-String ":$port.*LISTENING" | ForEach-Object {
        Write-Host "  $($_.Line.Trim())"
    }
    # 注意：这里必须用单引号包住内层命令。
    # 早先写成 \"ps aux | grep ...\" —— PowerShell 不认 \" 转义，
    # 字符串在那里就结束了，`|` 被当成管道符，报 CommandNotFoundException。
    Write-Host "  可尝试：wsl -d $distro -e bash -lc 'ps aux | grep custom_voice'" -ForegroundColor Yellow
    exit 1
}

Write-Host "[OK] 端口 $port 已释放" -ForegroundColor Green
Write-Host "     回到网页点「刷新状态」即可看到 TTS 已停止。" -ForegroundColor DarkGray
exit 0