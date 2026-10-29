# Dot-source in PowerShell: . 'D:\ai_pet_badge_dev\tools\Enter-Project.ps1'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectVenv = Join-Path $projectRoot '.venv'
$projectPython = Join-Path $projectVenv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { throw 'Project Python environment is missing.' }
$env:VIRTUAL_ENV = $projectVenv
$env:PATH = (Join-Path $projectVenv 'Scripts') + ';C:\Users\34262\AppData\Local\Programs\Microsoft VS Code\bin;' + $env:PATH
if ($env:NO_PROXY) {
    $env:NO_PROXY = (($env:NO_PROXY -split ',') | ForEach-Object {
        if ($_.Trim() -in @('::1/128', '[::1]', '[::1]/128')) { '::1' } else { $_ }
    }) -join ','
}
Set-Location -LiteralPath $projectRoot
Write-Output ('Desktop Python: ' + $projectPython)
