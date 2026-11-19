# 后台常驻启动 zjy14 TTS（WSL）
#
# 为什么需要这个脚本：
#   直接用 pwsh 的 background job 启动，job 会随工具调用结束而终止，
#   表现为「服务莫名其妙停了」。
#   改用 Start-Process 起一个独立的 powershell 窗口/进程，
#   它的生命周期不依赖当前会话。
#
# 用法：
#   .\start_tts_detached.ps1            # 启动
#   .\start_tts_detached.ps1 -Stop      # 停止
#   .\start_tts_detached.ps1 -Status    # 查看

param(
    [switch]$Stop,
    [switch]$Status,
    [int]$Port = 50001
)

$ErrorActionPreference = "Continue"
$logFile = "D:\xiangmudaima\zjy14\_tts_service.log"
$pidFile = "D:\xiangmudaima\zjy14\_tts_service.pid"

function Test-Port([int]$p) {
    return (Test-NetConnection -ComputerName 127.0.0.1 -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue)
}

if ($Status) {
    Write-Host "=== zjy14 TTS 状态 ===" -ForegroundColor Cyan
    $up = Test-Port $Port
    Write-Host "  端口 ${Port}: $(if($up){'在跑'}else{'已停'})"
    if ($up) {
        try {
            $h = Invoke-RestMethod -Uri "http://127.0.0.1:${Port}/health" -TimeoutSec 5
            Write-Host "  引擎: $($h.engine)  采样率: $($h.sample_rate)  音色: $($h.voices.voice_id -join ',')"
        } catch {
            Write-Host "  /health 不可用: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    if (Test-Path $pidFile) {
        Write-Host "  记录的 PID: $(Get-Content $pidFile -Raw)"
    }
    exit 0
}

if ($Stop) {
    Write-Host "=== 停止 zjy14 TTS ===" -ForegroundColor Yellow
    # 通过 WSL 找到并杀掉 uvicorn 进程
    $killed = wsl -e bash -lc "pkill -f 'custom_voice.server' && echo killed || echo none" 2>&1
    Write-Host "  $killed"
    if (Test-Path $pidFile) { Remove-Item $pidFile -Force }
    Start-Sleep -Seconds 2
    Write-Host "  端口 ${Port}: $(if(Test-Port $Port){'仍在跑'}else{'已停'})"
    exit 0
}

# 已在跑就不重复启动
if (Test-Port $Port) {
    Write-Host "[跳过] 端口 $Port 已在监听" -ForegroundColor Green
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:${Port}/health" -TimeoutSec 5
        Write-Host "  引擎: $($h.engine)  音色: $($h.voices.voice_id -join ',')"
    } catch {}
    exit 0
}

Write-Host "=== 启动 zjy14 TTS（后台常驻）===" -ForegroundColor Cyan

# 用 setsid + nohup 让进程脱离终端，日志重定向到文件
$inner = @"
cd /mnt/d/xiangmudaima/zjy14
source ~/cosyvoice-venv/bin/activate
export PYTHONPATH=/mnt/d/xiangmudaima/zjy14
export ZJY14_ENGINE=cosyvoice3
export ZJY14_PORT=$Port
export ZJY14_TEXT_FRONTEND=wetext
exec python -m custom_voice.server
"@
# 写到 WSL 可见的脚本文件（避免引号转义问题）
$shPath = "/tmp/start_zjy14_tts.sh"
# 注意：这里不用 [Encoding]::UTF8.GetBytes($str, $enc) 双参重载
# （PowerShell 5.1 没有该重载，会报 Cannot find an overload）。
# 用 here-string 直接通过 bash 写入即可。
try {
    wsl -e bash -lc "cat > $shPath <<'EOS'
cd /mnt/d/xiangmudaima/zjy14
source ~/cosyvoice-venv/bin/activate
export PYTHONPATH=/mnt/d/xiangmudaima/zjy14
export ZJY14_ENGINE=cosyvoice3
export ZJY14_PORT=$Port
export ZJY14_TEXT_FRONTEND=wetext
exec python -m custom_voice.server
EOS
chmod +x $shPath"
} catch {
    Write-Host "  写脚本失败，改用直接命令" -ForegroundColor Yellow
}

Write-Host "[1/2] 后台启动（日志: $logFile）..."
# 注意：$! 必须转义成 \$!，否则 PowerShell 会当变量插值成空串。
$launch = wsl -e bash -lc "setsid nohup bash $shPath > /mnt/d/xiangmudaima/zjy14/_tts_service.log 2>&1 < /dev/null & echo `$!"
Write-Host "  WSL PID: $launch"
$launch | Out-File -FilePath $pidFile -Encoding ascii

Write-Host "[2/2] 等待就绪（最多 60 秒）..."
$ready = $false
for ($i = 1; $i -le 60; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Port $Port) {
        try {
            $h = Invoke-RestMethod -Uri "http://127.0.0.1:${Port}/health" -TimeoutSec 3
            if ($h.ok) {
                Write-Host "  [OK] 就绪（${i}s）引擎=$($h.engine) 音色=$($h.voices.voice_id -join ',')" -ForegroundColor Green
                $ready = $true
                break
            }
        } catch {}
    }
}
if (-not $ready) {
    Write-Host "  [失败] 60 秒内未就绪，日志尾部：" -ForegroundColor Red
    if (Test-Path $logFile) { Get-Content $logFile -Tail 15 | ForEach-Object { "    $_" } }
    exit 1
}
