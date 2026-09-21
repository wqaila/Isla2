@echo off
chcp 65001 >nul
echo ============================================
echo   🌸 爱莉希雅 AI - 公网网页发布
echo ============================================
echo.

cd /d "%~dp0"

echo [1/3] 清理旧进程...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8088 " ^| findstr "LISTENING"') do (
    echo   关闭占用 8088 端口的进程 PID:%%a
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo [2/3] 启动本地 HTTP 服务器 (端口 8088)...
start "Elysia-WebServer" python -m http.server 8088 --bind 0.0.0.0
echo   等待服务器就绪...
timeout /t 2 /nobreak >nul

echo.
echo [3/3] 启动 Cloudflare Tunnel...
echo.
echo 等待 Cloudflare 分配公网地址...
echo.
echo ============================================================
echo  启动后公网访问地址（将域名替换为下方生成的地址）：
echo.
echo   🌸 首页：  你的隧道地址/index.html
echo   📁 汇总：  你的隧道地址/projects.html
echo ============================================================
echo.

"..\server\cloudflared-windows-amd64.exe" tunnel --url http://localhost:8088

echo.
echo 隧道已关闭。
pause
