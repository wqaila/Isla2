@echo off
chcp 65001 >nul
rem =====================================================================
rem  B 站视频/音频采集 —— 本地网页服务（双击启动）
rem
rem  ⚠️ 用本目录 venv 里的 python 跑，不要用系统 python：
rem     系统 python 可能没装 yt-dlp / flask，表现是「一启动就报缺模块」。
rem  ⚠️ 必须设 PYTHONIOENCODING=utf-8：控制台代码页是 GBK 时，
rem     子进程打印 ✓ / ✗ 这类字符会直接 UnicodeEncodeError 崩掉。
rem =====================================================================
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [错误] 找不到 venv：%PY%
    echo         请先在本目录运行：python -m venv .venv
    pause
    exit /b 1
)

set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

echo 正在启动 B 站采集服务，浏览器会自动打开…
"%PY%" -m bilibili_downloader.web_service --port 8770

rem 服务退出后停一下，别让窗口一闪而过（看不到报错）
echo.
echo 服务已停止。
pause
