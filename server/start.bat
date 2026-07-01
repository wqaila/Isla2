@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
echo ========================================
echo   爱莉希雅 AI 聊天服务 - 启动脚本
echo ========================================
echo.

cd /d "%~dp0"

REM ===== 1. 先杀掉占用 8080 端口的旧进程 =====
echo [INFO] 检查端口 8080 是否被占用...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8080 ^| findstr LISTENING') do (
    echo [INFO] 发现旧进程 PID=%%a，正在终止...
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM ===== 2. 检查并启动 Ollama =====
echo [INFO] 检查 Ollama 服务状态...
set OLLAMA_READY=0
REM 先检查 Ollama 是否已在运行（通过 API 端口检测）
curl -s -o NUL -f http://localhost:11434/api/tags 2>nul
if %errorlevel% equ 0 (
    echo [INFO] Ollama 已在运行
    set OLLAMA_READY=1
) else (
    echo [INFO] Ollama 未运行，正在启动...
    start "" "C:\Users\431\AppData\Local\Programs\Ollama\ollama.exe" serve
    echo [INFO] 等待 Ollama 就绪（最多 30 秒）...
    for /L %%i in (1,1,30) do (
        timeout /t 1 /nobreak >nul
        curl -s http://localhost:11434/api/tags >nul 2>&1
        if !errorlevel! equ 0 (
            echo.
            echo [INFO] Ollama 已就绪！(耗时 %%i 秒)
            set OLLAMA_READY=1
            goto :ollama_done
        )
        <nul set /p "=."
    )
    :ollama_done
    echo.
    if "!OLLAMA_READY!"=="0" (
        echo [WARN] Ollama 启动超时，服务将运行但模型可能不可用
    )
)
echo.

REM ===== 3. 检查虚拟环境 =====
if not exist "venv\Scripts\python.exe" (
    echo [INFO] 虚拟环境不存在，正在创建...
    python -m venv venv
    echo [INFO] 虚拟环境创建完成！
    echo.
)

REM ===== 4. 安装依赖 =====
echo [INFO] 检查并安装依赖...
set PYTHONUTF8=1
venv\Scripts\pip.exe install -r requirements.txt --quiet --disable-pip-version-check
echo [INFO] 依赖检查完成！
echo.

REM ===== 5. 启动服务 =====
echo [INFO] 启动服务...
echo [INFO] 监控面板: http://localhost:8080/dashboard
echo [INFO] API 文档: http://localhost:8080/docs
echo [INFO] 按 Ctrl+C 停止服务
echo.

venv\Scripts\python.exe main.py

pause