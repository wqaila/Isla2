# zjy11 固件构建环境（复用本机已有的 ESP-IDF 5.5.5）
#
# 背景：本机 D:\esp 下已有一套完整安装（含 esp32s3 工具链），
# 但官方 export.ps1 只会去 C:\Users\<user>\.espressif 找 Python 环境，
# 路径不匹配导致激活失败。本脚本直接调用官方 activate.py 生成正确环境。
#
# 用法（每次新开终端构建前，先执行一次）：
#   . .\firmware\idf-env.ps1

$ErrorActionPreference = 'Stop'

$IdfPath = 'D:\esp\esp-idf-5.5.5'
$IdfToolsPath = 'D:\esp\.espressif'
$IdfVenv = Join-Path $IdfToolsPath 'python_env\idf5.5_py3.11_env\Scripts\python.exe'

$missing = @()
foreach ($p in @($IdfPath, $IdfToolsPath, $IdfVenv)) {
    if (-not (Test-Path -LiteralPath $p)) {
        $missing += $p
    }
}
if ($missing.Count -gt 0) {
    throw ('未找到 ESP-IDF 组件：' + ($missing -join '; ') + ' —— 请确认 D:\esp 下的安装是否完整。')
}

$env:IDF_PATH = $IdfPath
$env:IDF_TOOLS_PATH = $IdfToolsPath
$env:ESP_IDF_VERSION = '5.5'

# 官方脚本会生成一个 PowerShell 激活文件，路径打印在 stdout
$activateScript = & $IdfVenv "$IdfPath\tools\activate.py" --export 2>$null
if (-not $activateScript -or -not (Test-Path -LiteralPath $activateScript)) {
    throw '无法生成 ESP-IDF 激活脚本，请检查安装。'
}

. $activateScript

Write-Host ('ESP-IDF 环境已就绪：' + $env:ESP_IDF_VERSION) -ForegroundColor Green
Write-Host ('IDF_PATH = ' + $env:IDF_PATH)
Write-Host '目标芯片 = esp32s3'
