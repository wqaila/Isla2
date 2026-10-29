# 烧录 ESP32-S3 固件
#
# 用法：
#   .\scripts\flash_firmware.ps1 -Port COM7
#   .\scripts\flash_firmware.ps1 -Port COM7 -Build      # 先编译再烧
#   .\scripts\flash_firmware.ps1 -Port COM7 -Erase      # 擦除后烧（干净）
#   .\scripts\flash_firmware.ps1 -Restore               # 刷回原厂固件
#
# 重要：烧录会**整片覆盖**原有固件（ai_pet_badge）。
#       原固件已备份在 backup/esp32/，用 -Restore 可随时还原。

param(
    [string]$Port = "COM7",
    [switch]$Build,
    [switch]$Erase,
    [switch]$Restore,
    [string]$Backup = "esp32s3_full_16MB_20261028_092303.bin"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$fw = Join-Path $root "firmware"

$idfTools   = "D:\esp\.espressif"
$idfPath    = "D:\esp\esp-idf-5.5.5"
$exportPs1  = Join-Path $idfPath "export.ps1"

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

# ---------------------------------------------------------------- 还原
if ($Restore) {
    $bin = Join-Path $root "backup\esp32\$Backup"
    if (-not (Test-Path $bin)) {
        Write-Host "备份不存在: $bin" -ForegroundColor Red
        exit 1
    }
    Write-Host "即将整片回写 16MB 原厂固件：$bin" -ForegroundColor Yellow
    Write-Host "这会覆盖当前固件，请确认。" -ForegroundColor Yellow

    $py = Join-Path $root ".venv\Scripts\python.exe"
    & $py -m esptool --port $Port --baud 921600 write-flash 0 $bin
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n还原完成。按 RESET 键重启。" -ForegroundColor Green
    } else {
        Write-Host "`n还原失败，退出码 $LASTEXITCODE" -ForegroundColor Red
        exit $LASTEXITCODE
    }
    exit 0
}

# ---------------------------------------------------------------- 环境
Write-Step "激活 ESP-IDF 5.5.5"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
$env:IDF_TOOLS_PATH = $idfTools
$env:IDF_PATH = $idfPath
& $exportPs1 | Out-Null
if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
    Write-Host "idf.py 不可用，检查 $exportPs1" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- 编译
if ($Build) {
    Write-Step "编译"
    Push-Location $fw
    & idf.py build
    $ok = $LASTEXITCODE -eq 0
    Pop-Location
    if (-not $ok) {
        Write-Host "编译失败" -ForegroundColor Red
        exit 1
    }
}

$appBin = Join-Path $fw "build\zjy13_voice.bin"
if (-not (Test-Path $appBin)) {
    Write-Host "未找到固件产物: $appBin`n请先运行 -Build" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------- 烧录
if ($Erase) {
    Write-Step "擦除整片 Flash"
    Write-Host "擦除后 Wi-Fi 凭据等 NVS 数据会全部丢失。" -ForegroundColor Yellow
    Push-Location $fw
    & idf.py -p $Port erase-flash
    Pop-Location
}

Write-Step "烧录到 $Port"
Push-Location $fw
& idf.py -p $Port flash
$rc = $LASTEXITCODE
Pop-Location

if ($rc -ne 0) {
    Write-Host "`n烧录失败，退出码 $rc" -ForegroundColor Red
    Write-Host "常见原因：串口被占用 / 波特率过高 / 板子未进下载模式" -ForegroundColor Yellow
    Write-Host "  1. 关闭占用 COM 口的程序（串口助手、Arduino IDE 等）"
    Write-Host "  2. 改用 -Baud 460800 或更低"
    Write-Host "  3. 按住 BOOT(GPIO0) 再按 RESET，松开 RESET 后松开 BOOT"
    exit $rc
}

Write-Step "完成"
Write-Host "查看日志：idf.py -p $Port monitor" -ForegroundColor Green
Write-Host "退出 monitor：Ctrl + ]" -ForegroundColor Green
