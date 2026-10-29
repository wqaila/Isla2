@echo off
where pwsh >nul 2>nul
if errorlevel 1 (
  powershell.exe -NoProfile -File "%~dp0Switch-Character.ps1"
) else (
  pwsh.exe -NoProfile -File "%~dp0Switch-Character.ps1"
)
pause
