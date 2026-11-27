@echo off
chcp 936 >nul
rem zjy14 · 网页一键启动
rem
rem 双击本文件 → 自动起「启动器 + 网页 dev 服务器」→ 打开「语音链路启动」页。
rem 等价于: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_web.ps1 -OpenBrowser
rem
rem 想把语音链路（ASR/LLM/TTS/网关）也一起拉起来，用命令行加 -WithServices：
rem   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_web.ps1 -WithServices

setlocal
cd /d "%~dp0"

if not exist "scripts\start_web.ps1" (
    echo [错误] 找不到 scripts\start_web.ps1
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\start_web.ps1"
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
    echo.
    echo [提示] 退出码 %RC%
    pause
)
endlocal
exit /b %RC%
