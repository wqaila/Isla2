@echo off
chcp 65001 >nul
echo ============================================
echo   爱莉希雅 AI - Cloudflare Tunnel 启动脚本
echo ============================================
echo.

:: 优先使用本地的 cloudflared exe
set CLOUDFLARED=cloudflared-windows-amd64.exe
if not exist "%CLOUDFLARED%" (
    :: 回退到系统 PATH 中的 cloudflared
    set CLOUDFLARED=cloudflared
)

echo [启动] Cloudflare Quick Tunnel（无需登录，免费）
echo.
echo   公网用户可以通过生成的 HTTPS URL 访问服务
echo   URL 格式类似: https://xxx-xxx-xxx.trycloudflare.com
echo.
echo   支持的功能：
echo     - Web 管理面板: /dashboard
echo     - API 文档: /docs
echo     - 聊天 API: /api/chat
echo     - WebSocket: wss://域名/ws/chat
echo.
echo   按 Ctrl+C 停止隧道
echo ============================================
echo.

%CLOUDFLARED% tunnel --url http://localhost:8080

pause
