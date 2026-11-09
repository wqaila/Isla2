@echo off
REM ============================================================
REM  One-click launcher: TTS (WSL2) + WebSocket server
REM
REM  NOTE: this file is intentionally ASCII-only.
REM  Windows cmd reads .bat files with the OEM code page (GBK/936
REM  on Chinese Windows). UTF-8 Chinese bytes get misread, and a
REM  UTF-8 lead byte followed by a newline eats the line break,
REM  merging lines and truncating commands. All Chinese messages
REM  live in scripts\start_all.ps1 (UTF-8 with BOM) instead.
REM
REM  Usage:
REM    double-click this file, or
REM    powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -BindHost 0.0.0.0
REM ============================================================

cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title AI-Role Launcher

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start_all.ps1" -BindHost 0.0.0.0

if errorlevel 1 (
  echo.
  echo [Launcher did not finish] See the messages above.
  echo.
  echo Tips:
  echo   - show status : powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -Status
  echo   - first TTS run downloads models, retry with -TTSTimeout 300
  pause
)
