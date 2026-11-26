# 仅停止 zjy14 的 TTS（WSL 内的 uvicorn）
#
# 用法（在 zjy14 目录下）：
#   .\scripts\stop_tts.ps1
#
# 为什么单独一个脚本：
#   控制台（launcher）在**受限环境**（被沙箱/作业对象限制的非交互进程）里
#   启动的 Python 无法访问 WSL 发行版，`wsl.exe` 会返回 Wsl/E_ACCESSDENIED。
#   WSL 里的 uvicorn 又不会随 Windows 侧的 taskkill 一起退出
#   （Windows 上看到的监听 PID 是 WSL 端口转发进程）。
#   所以 WSL 侧服务需要从你自己的 PowerShell 里停 —— 这个脚本就是那一步。

$ErrorActionPreference = "Continue"

$distro = "Ubuntu-24.04"

Write-Host "=== 停止 zjy14 TTS（WSL: $distro）===" -ForegroundColor Cyan

# pkill 找到进程时退出码为 0，找不到为 1
& wsl.exe -d $distro -e bash -lc "pkill -f 'custom_voice.server' && echo KILLED || echo NONE"
$rc = $LASTEXITCODE

Start-Sleep -Seconds 2

$listening = netstat -ano -p TCP | Select-String ":50001.*LISTENING"
if ($listening) {
    Write-Host "[警告] 端口 50001 仍在监听：" -ForegroundColor Yellow
    $listening | ForEach-Object { Write-Host "  $($_.Line.Trim())" }
    Write-Host "  可尝试：wsl -d $distro -e bash -lc \"ps aux | grep custom_voice\"" -ForegroundColor Yellow
    exit 1
}

Write-Host "[OK] 端口 50001 已释放" -ForegroundColor Green
exit 0