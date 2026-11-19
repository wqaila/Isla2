# 后台常驻启动 zjy13 语音服务
#
# 与 start_voice_server.ps1 的区别：
#   那个是前台运行（关掉终端就停），这个是 detached 常驻，
#   日志落文件，适合联调时长时间挂着。
#
# 用法：
#   .\start_voice_server_detached.ps1            # 启动
#   .\start_voice_server_detached.ps1 -Stop      # 停止
#   .\start_voice_server_detached.ps1 -Status    # 查看

param(
    [switch]$Stop,
    [switch]$Status,
    [string]$VenvPython = "D:\xiangmudaima\zjy13\.venv-asr\Scripts\python.exe",
    [string]$ProjectDir = "D:\xiangmudaima\zjy13",
    [string]$HostAddr = "0.0.0.0",
    [int]$Port = 8765,
    [string]$TtsBaseUrl = "http://127.0.0.1:50001",
    [string]$CharacterFile = "server\characters\elysia.yaml"
)

$ErrorActionPreference = "Continue"
$logFile = "D:\xiangmudaima\zjy13\logs\ws_service_live.log"

function Test-Port([int]$p) {
    return (Test-NetConnection -ComputerName 127.0.0.1 -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue)
}

if ($Status) {
    Write-Host "=== zjy13 语音服务状态 ===" -ForegroundColor Cyan
    $up = Test-Port $Port
    Write-Host "  端口 ${Port}: $(if($up){'在跑'}else{'已停'})"
    if ($up) {
        try {
            $d = Invoke-RestMethod -Uri "http://127.0.0.1:${Port}/api/devices" -TimeoutSec 5
            Write-Host "  角色: $($d.server.character_name)"
            Write-Host "  连接: $($d.counters.active)/$($d.server.max_connections)"
            Write-Host "  累计: 连接 $($d.counters.total_connections) 轮次 $($d.counters.total_turns) 错误 $($d.counters.total_errors) 拒绝 $($d.counters.rejected_connections)"
            foreach ($c in $d.clients) {
                Write-Host "  --- $($c.device_id) ---"
                Write-Host "    state=$($c.state)  peer=$($c.peer)  心跳=$($c.last_heartbeat_s)s"
                Write-Host "    对话=$($c.turns_done)轮  ASR=$($c.timings.asr_ms) LLM首=$($c.timings.llm_first_ms) TTS首=$($c.timings.tts_first_ms)"
                if ($c.last_asr_text) { Write-Host "    识别: $($c.last_asr_text)" }
                if ($c.last_reply_text) { Write-Host "    回复: $($c.last_reply_text)" }
                if ($c.errors_recent.Count -gt 0) {
                    foreach ($e in $c.errors_recent) { Write-Host "    [错误] $($e.code): $($e.message)" -ForegroundColor Red }
                }
            }
        } catch {
            Write-Host "  /api/devices 不可用: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    if (Test-Path $logFile) {
        Write-Host "  日志: $logFile ($((Get-Item $logFile).Length) 字节)"
    }
    exit 0
}

if ($Stop) {
    Write-Host "=== 停止 zjy13 ===" -ForegroundColor Yellow
    $n = 0
    Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
        $ci = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue
        if ($ci -and $ci.CommandLine -match 'server\.ws\.server') {
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            $n++
        }
    }
    Write-Host "  已停止 $n 个进程"
    Start-Sleep -Seconds 2
    Write-Host "  端口 ${Port}: $(if(Test-Port $Port){'仍在跑'}else{'已停'})"
    exit 0
}

if (Test-Port $Port) {
    Write-Host "[跳过] 端口 $Port 已在监听" -ForegroundColor Green
    exit 0
}

# 预检 TTS
try {
    $h = Invoke-RestMethod -Uri "$TtsBaseUrl/health" -TimeoutSec 5
    Write-Host "[预检] TTS 可达: 引擎=$($h.engine)" -ForegroundColor Green
} catch {
    Write-Host "[错误] TTS 不可达，请先启动 zjy14：" -ForegroundColor Red
    Write-Host "  powershell -File D:\xiangmudaima\zjy14\scripts\start_tts_detached.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host "=== 启动 zjy13（后台常驻）===" -ForegroundColor Cyan
Write-Host "  监听: ${HostAddr}:${Port}   TTS: $TtsBaseUrl"
Write-Host "  日志: $logFile"

# 用 Start-Process 起独立进程，生命周期不依赖当前会话
$args = @(
    "-m", "server.ws.server",
    "--host", $HostAddr,
    "--port", "$Port",
    "--character-file", $CharacterFile
)

$env:PYTHONPATH = $ProjectDir
$env:TTS_BASE_URL = $TtsBaseUrl
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

$proc = Start-Process -FilePath $VenvPython `
    -ArgumentList $args `
    -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError "$logFile.err" `
    -WindowStyle Hidden `
    -PassThru

Write-Host "  PID: $($proc.Id)"
Write-Host "[等待] 就绪（最多 120 秒，ASR 模型加载较慢）..."
$ready = $false
for ($i = 1; $i -le 120; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Port $Port) {
        try {
            $d = Invoke-RestMethod -Uri "http://127.0.0.1:${Port}/api/devices" -TimeoutSec 3
            Write-Host "  [OK] 就绪（${i}s）角色=$($d.server.character_name)" -ForegroundColor Green
            $ready = $true
            break
        } catch {}
    }
    if ($proc.HasExited) {
        Write-Host "  [失败] 进程已退出（退出码 $($proc.ExitCode)）" -ForegroundColor Red
        break
    }
}
if (-not $ready) {
    Write-Host "  [失败] 未就绪，日志尾部：" -ForegroundColor Red
    if (Test-Path $logFile) { Get-Content $logFile -Tail 20 | ForEach-Object { "    $_" } }
    if (Test-Path "$logFile.err") { Get-Content "$logFile.err" -Tail 20 | ForEach-Object { "    $_" } }
    exit 1
}
