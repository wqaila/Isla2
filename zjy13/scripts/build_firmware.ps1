# 编译 zjy13 固件
#
# 关键：必须设 IDF_TOOLS_PATH=D:\esp\.espressif
#   否则 IDF 会去 C:\Users\<user>\.espressif 找 venv，报
#   "ESP-IDF Python virtual environment ... not found"。
#   本机 IDF 装在 D:\esp\，但从未设置过该环境变量。

param(
    [string]$IdfPath = "D:\esp\esp-idf-5.5.5",
    [string]$ToolsPath = "D:\esp\.espressif",
    [string]$ProjectDir = "D:\xiangmudaima\zjy13\firmware",
    [switch]$Clean
)

$ErrorActionPreference = "Continue"

Write-Host "=== 编译 zjy13 固件 ===" -ForegroundColor Cyan
Write-Host "  IDF      : $IdfPath"
Write-Host "  Tools    : $ToolsPath"
Write-Host "  Project  : $ProjectDir"
Write-Host ""

if (-not (Test-Path $IdfPath)) { Write-Host "[错误] IDF 路径不存在" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $ProjectDir)) { Write-Host "[错误] 项目路径不存在" -ForegroundColor Red; exit 1 }

$env:IDF_TOOLS_PATH = $ToolsPath
Set-Location $IdfPath
. .\export.ps1 2>&1 | Out-Null

if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
    Write-Host "[错误] idf.py 激活失败" -ForegroundColor Red
    exit 1
}

Set-Location $ProjectDir

if ($Clean) {
    Write-Host "[1/2] 全量重编（idf.py fullclean）..." -ForegroundColor Yellow
    & idf.py fullclean 2>&1 | Out-Null
}

Write-Host "[编译] idf.py build ..." -ForegroundColor Yellow
& idf.py build 2>&1 | ForEach-Object { $_ }

$code = $LASTEXITCODE
Write-Host ""
if ($code -eq 0) {
    Write-Host "=== 编译成功 ===" -ForegroundColor Green
    $bin = Join-Path $ProjectDir "build\ai_pet_badge.bin"
    if (Test-Path $bin) {
        $size = (Get-Item $bin).Length
        Write-Host "  固件: $bin ($([math]::Round($size/1024)) KB)"
    }
} else {
    Write-Host "=== 编译失败（退出码 $code）===" -ForegroundColor Red
}
exit $code
