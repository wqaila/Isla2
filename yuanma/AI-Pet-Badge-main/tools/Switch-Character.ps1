param(
    [ValidateSet('qinglan','elysia','')][string]$Character = '',
    [string]$Port,
    [switch]$VerifyOnly,
    [string]$BundleRoot = $PSScriptRoot,
    [string]$Python = 'C:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe'
)
$ErrorActionPreference = 'Stop'
if (-not $Character) {
    Write-Output '1 - Qinglan (standing idle)'
    Write-Output '2 - Elysia (seated idle)'
    $characterChoice = Read-Host 'Choose 1 or 2, or 0 to exit'
    if ($characterChoice -eq '0') { return }
    if ($characterChoice -eq '1') { $Character = 'qinglan' }
    elseif ($characterChoice -eq '2') { $Character = 'elysia' }
    else { throw 'Invalid choice.' }
}
$characterFolder = Join-Path $BundleRoot $Character
$characterFlasher = Join-Path $characterFolder 'Flash-Badge.ps1'
if (-not (Test-Path -LiteralPath $characterFlasher)) { throw 'Character bundle missing. Set -BundleRoot to the exported folder.' }
if ($VerifyOnly) {
    & $characterFlasher -VerifyOnly -ExpectedCharacter $Character
    return
}
if (-not (Test-Path -LiteralPath $Python)) { throw 'Supply ESP-IDF Python with -Python.' }
if (-not $Port) {
    $characterPorts = @(& $Python -c 'from serial.tools import list_ports; print("\n".join(p.device for p in list_ports.comports() if p.vid==0x303a and p.pid==0x1001))')
    if ($LASTEXITCODE -ne 0) { throw 'USB port detection failed.' }
    $characterPorts = @($characterPorts | Where-Object { $_ -match '^COM\d+$' })
    if ($characterPorts.Count -eq 1) { $Port = $characterPorts[0] }
    else { $Port = Read-Host 'Enter the confirmed ESP32 USB port (for example COM9)' }
}
if ($Port -notmatch '^COM\d+$') { throw 'Expected a Windows COM port.' }
Write-Output "Switching to $Character on $Port..."
& $characterFlasher -Port $Port -Python $Python -ExpectedCharacter $Character
