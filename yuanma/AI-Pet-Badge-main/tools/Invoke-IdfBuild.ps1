param(
    [ValidateSet('02_lvgl_demo','04_esp_wakeword_det','01_comprehensive_example')]
    [string]$Example = '02_lvgl_demo',
    [ValidateRange(1,16)][int]$Jobs = 6
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Enter-Idf.ps1')
$buildProject = Join-Path (Join-Path (Split-Path -Parent $PSScriptRoot) 'firmware') $Example
Push-Location -LiteralPath $buildProject
try {
    python (Join-Path $env:IDF_PATH 'tools\idf.py') reconfigure
    if ($LASTEXITCODE -ne 0) { throw ('Configure failed with exit code ' + $LASTEXITCODE) }
    cmake --build build --parallel $Jobs
    if ($LASTEXITCODE -ne 0) { throw ('Build failed with exit code ' + $LASTEXITCODE) }
} finally {
    Pop-Location
}
