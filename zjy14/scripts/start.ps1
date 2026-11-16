# zjy14 一键启动（Windows 侧）
#
# 用法（在 Windows PowerShell 中）：
#   .\scripts\start.ps1              # 默认 stub 引擎
#   .\scripts\start.ps1 cosyvoice3   # 真模型
#   .\scripts\start.ps1 cosyvoice3 -Port 50002
#
# 作用：
#   1. 检查 WSL 是否可用
#   2. 检查启动脚本存在
#   3. 在 WSL 中启动服务（前台，Ctrl+C 停止）
#
# 为什么用 WSL：
#   CosyVoice3 的依赖（deepspeed / onnxruntime-gpu / tensorrt）官方只支持 Linux。
#   Windows 侧只放代码与前端，模型推理全部在 WSL。
#
# 兼容性：只用 PowerShell 5.1 支持的语法（不用 && / ||）。

param(
    [string]$Engine = "stub",
    [int]$Port = 50001
)

$ErrorActionPreference = "Stop"
$ProjectDir = "/mnt/d/xiangmudaima/zjy14"
$StartScript = "$ProjectDir/scripts/start.sh"

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  zjy14 Custom Voice Engine" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ---- 检查 WSL ----
Write-Host ""
Write-Host "[1/3] 检查 WSL..." -ForegroundColor Yellow

$distros = @()
$wslOk = $true
try {
    $raw = & wsl.exe --list --quiet
    if ($LASTEXITCODE -ne 0) {
        $wslOk = $false
    } else {
        foreach ($line in $raw) {
            $t = "$line".Trim()
            if ($t.Length -gt 0) {
                $distros += $t
            }
        }
    }
} catch {
    $wslOk = $false
}

if (-not $wslOk) {
    Write-Host "  [错误] WSL 不可用" -ForegroundColor Red
    Write-Host "  请先安装 WSL2 与 Ubuntu 24.04" -ForegroundColor Red
    exit 1
}

if ($distros.Count -eq 0) {
    Write-Host "  [错误] 没有已安装的 WSL 发行版" -ForegroundColor Red
    exit 1
}
Write-Host "  可用发行版：$($distros -join ', ')" -ForegroundColor Green

# ---- 检查启动脚本 ----
Write-Host ""
Write-Host "[2/3] 检查项目..." -ForegroundColor Yellow
# 注意：PowerShell 5.1 解析器会把字符串里的 && / || 当语法错误，
# 因此这里用 test -f 的返回值判断，不在命令里用 shell 逻辑运算符。
$null = & wsl -e bash -lc "test -f $StartScript" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [错误] 启动脚本不存在：$StartScript" -ForegroundColor Red
    exit 1
}
Write-Host "  启动脚本就绪" -ForegroundColor Green

# ---- 启动 ----
Write-Host ""
Write-Host "[3/3] 启动服务（引擎=$Engine，端口=$Port）" -ForegroundColor Yellow
Write-Host "  API 文档：http://127.0.0.1:$Port/docs" -ForegroundColor Green
Write-Host "  健康检查：http://127.0.0.1:$Port/health" -ForegroundColor Green
Write-Host "  Ctrl+C 停止" -ForegroundColor Green
Write-Host ""

# 用分号而非 && 串联命令（PowerShell 5.1 解析器会把字符串里的 && 当语法错误）
$cmd = "cd $ProjectDir; ZJY14_PORT=$Port bash $StartScript $Engine"
& wsl.exe -e bash -lc $cmd

$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Host ""
    Write-Host "[提示] 服务退出码 $code" -ForegroundColor Yellow
    Write-Host "  排查步骤见 docs/TROUBLESHOOTING.md" -ForegroundColor Yellow
    exit $code
}
