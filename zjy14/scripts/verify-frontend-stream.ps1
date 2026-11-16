# Phase 8 前端流式验收（Windows 侧，需要 node）
#
# 用法：
#   .\scripts\verify-frontend-stream.ps1
#   .\scripts\verify-frontend-stream.ps1 -Port 50010
#
# 前置：后端已启动（真模型）。
# 作用：用与前端完全相同的解析算法消费真实流，验证：
#   分块边界、结束标记、采样率、首块延迟

param(
    [int]$Port = 50001
)

$ErrorActionPreference = "Stop"
$WebDir = "D:\xiangmudaima\zjy14\web"
$Base = "http://127.0.0.1:$Port"

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " Phase 8 前端流式验收（Windows 侧）" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ---- 检查 node ----
Write-Host ""
Write-Host "[1/3] 检查 node..." -ForegroundColor Yellow
$nodeVersion = $null
try {
    $nodeVersion = & node --version 2>$null
} catch {
    $nodeVersion = $null
}
if (-not $nodeVersion) {
    Write-Host "  [错误] node 不可用" -ForegroundColor Red
    Write-Host "  请安装 Node.js 或把它加入 PATH" -ForegroundColor Red
    exit 1
}
Write-Host "  node $nodeVersion" -ForegroundColor Green

# ---- 检查后端 ----
Write-Host ""
Write-Host "[2/3] 检查后端（$Base）..." -ForegroundColor Yellow
$healthy = $false
try {
    $h = Invoke-RestMethod -Uri "$Base/health" -TimeoutSec 5
    if ($h.ok) {
        $healthy = $true
        Write-Host "  引擎=$($h.engine)  采样率=$($h.sample_rate)  音色=$($h.voices.Count)" -ForegroundColor Green
    }
} catch {
    $healthy = $false
}

if (-not $healthy) {
    Write-Host "  [错误] 后端不可用" -ForegroundColor Red
    Write-Host "  请先启动：" -ForegroundColor Red
    Write-Host "    .\scripts\start.ps1 cosyvoice3 -Port $Port" -ForegroundColor Yellow
    exit 1
}

# ---- 跑流式验证 ----
Write-Host ""
Write-Host "[3/3] 流式解析验证..." -ForegroundColor Yellow
Write-Host ""

Push-Location $WebDir
try {
    $env:ZJY14_BASE = $Base
    & node scripts/verify-stream.mjs
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host ""
if ($code -eq 0) {
    Write-Host "==============================================" -ForegroundColor Green
    Write-Host " 验收通过" -ForegroundColor Green
    Write-Host "==============================================" -ForegroundColor Green
} else {
    Write-Host "==============================================" -ForegroundColor Red
    Write-Host " 验收失败（退出码 $code）" -ForegroundColor Red
    Write-Host "==============================================" -ForegroundColor Red
}

exit $code
