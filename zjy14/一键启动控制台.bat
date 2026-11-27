@echo off
chcp 936 >nul
rem zjy14 语音链路 · 一键启动控制台
rem
rem 双击本文件 → 打开控制台网页 → 点「一键启动全部」。
rem 等价于:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_launcher.ps1 -OpenBrowser

setlocal
cd /d "%~dp0"

if not exist "scripts\start_launcher.ps1" (
    echo [错误] 找不到 scripts\start_launcher.ps1
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start_launcher.ps1" -OpenBrowser
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
    echo.
    echo [提示] 控制台退出码 %RC%
    pause
)
endlocal
exit /b %RC%
