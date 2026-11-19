# 启动 zjy13 语音服务（Windows 侧，供板子通过热点连接）
#
# 关键点：
#   1. 必须用 --host 0.0.0.0，默认 127.0.0.1 板子访问不到
#   2. 必须用 .venv-asr（含 funasr + torch），不是 .venv
#   3. TTS 指向 zjy14（:50001），不是老的 :50000
#   4. 工作目录必须是 zjy13 根目录（server 包要靠 PYTHONPATH 找到）

param(
    [string]$VenvPython = "D:\xiangmudaima\zjy13\.venv-asr\Scripts\python.exe",
    [string]$ProjectDir = "D:\xiangmudaima\zjy13",
    [string]$HostAddr = "0.0.0.0",
    [int]$Port = 8765,
    [string]$TtsBaseUrl = "http://127.0.0.1:50001",
    # 注意：参数是 --character-file（接受 YAML 路径），不是角色 ID。
    # 传 "elysia" 会报「角色卡文件不存在: elysia」。
    [string]$CharacterFile = "server\characters\elysia.yaml"
)

$ErrorActionPreference = "Continue"

Write-Host "=== 启动 zjy13 语音服务 ===" -ForegroundColor Cyan
Write-Host "  监听    : ${HostAddr}:${Port}"
Write-Host "  TTS     : $TtsBaseUrl"
Write-Host "  角色卡  : $CharacterFile"
Write-Host "  Python  : $VenvPython"
Write-Host ""

if (-not (Test-Path $VenvPython)) {
    Write-Host "[错误] venv python 不存在: $VenvPython" -ForegroundColor Red
    exit 1
}

# 先确认 TTS 可达 —— 服务端启动时会校验，不通就直接退出
try {
    $h = Invoke-RestMethod -Uri "$TtsBaseUrl/health" -TimeoutSec 5
    Write-Host "[预检] TTS 可达: 引擎=$($h.engine) 音色=$($h.voices.voice_id -join ',')" -ForegroundColor Green
} catch {
    Write-Host "[警告] TTS 不可达: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  请先启动 zjy14：" -ForegroundColor Yellow
    Write-Host "    wsl -e bash -lc `"cd /mnt/d/xiangmudaima/zjy14 && source ~/cosyvoice-venv/bin/activate && PYTHONPATH=. ZJY14_ENGINE=cosyvoice3 ZJY14_PORT=50001 python -m custom_voice.server`"" -ForegroundColor Yellow
}

Set-Location $ProjectDir
$env:PYTHONPATH = $ProjectDir
$env:TTS_BASE_URL = $TtsBaseUrl
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[启动] 按 Ctrl+C 停止" -ForegroundColor Yellow
Write-Host ""

& $VenvPython -m server.ws.server --host $HostAddr --port $Port --character-file $CharacterFile 2>&1

exit $LASTEXITCODE
