param(
    [string]$Port,
    [switch]$VerifyOnly,
    [ValidateSet('qinglan','elysia','')][string]$ExpectedCharacter = '',
    [string]$Python = 'C:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'
$bundleManifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($ExpectedCharacter -and $bundleManifest.character -ne $ExpectedCharacter) { throw 'Wrong character bundle.' }
if ($bundleManifest.mode -ne 'shake_only' -or $bundleManifest.orientation_enabled -ne $false) { throw 'Wrong interaction mode.' }
foreach ($bundleFile in $bundleManifest.file_sha256.PSObject.Properties) {
    $bundlePath = Join-Path $PSScriptRoot $bundleFile.Name
    if ((Get-FileHash -LiteralPath $bundlePath -Algorithm SHA256).Hash -ne $bundleFile.Value) {
        throw "Checksum mismatch: $($bundleFile.Name)"
    }
}
Write-Output "Verified $($bundleManifest.character): fixed screen, shake stars."
if ($VerifyOnly) { return }
if (-not $Port) { throw 'Supply the confirmed ESP32 USB serial port with -Port.' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Supply ESP-IDF Python with -Python.' }
Push-Location -LiteralPath $PSScriptRoot
try {
    & $Python -m esptool --chip esp32s3 --port $Port --baud 921600 write_flash '@flash_args'
    if ($LASTEXITCODE -ne 0) { throw 'Flashing failed.' }
} finally { Pop-Location }
