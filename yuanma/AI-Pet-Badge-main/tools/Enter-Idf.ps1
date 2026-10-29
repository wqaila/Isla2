# Dot-source in PowerShell: . 'D:\ai_pet_badge_dev\tools\Enter-Idf.ps1'
$env:IDF_PATH = 'C:\Espressif\v5.5.3\esp-idf'
$env:IDF_TOOLS_PATH = 'C:\Espressif'
$env:IDF_PYTHON_ENV_PATH = 'C:\Espressif\python_env\idf5.5_py3.11_env'
$idfPython = Join-Path $env:IDF_PYTHON_ENV_PATH 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $idfPython)) { throw 'ESP-IDF Python environment is missing.' }
$env:PATH = (Split-Path -Parent $idfPython) + ';C:\Users\34262\AppData\Local\Programs\Python\Python311;' + $env:PATH
if ($env:NO_PROXY) {
    $env:NO_PROXY = (($env:NO_PROXY -split ',') | ForEach-Object {
        if ($_.Trim() -in @('::1/128', '[::1]', '[::1]/128')) { '::1' } else { $_ }
    }) -join ','
}
$previousErrorPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Stop'
    . 'C:\Espressif\v5.5.3\esp-idf\export.ps1'
} finally {
    $ErrorActionPreference = $previousErrorPreference
}
