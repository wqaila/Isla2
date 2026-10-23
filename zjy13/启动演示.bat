@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
title Xiaoman LLM Demo
if not exist ".venv\Scripts\python.exe" goto noenv
if not exist ".env" goto noenvfile
".venv\Scripts\python.exe" -m tools.web_demo
goto end
:noenv
echo [ERROR] .venv not found. Run: python -m venv .venv
pause
goto end
:noenvfile
echo [ERROR] .env not found. Copy .env.example to .env and fill in your key.
pause
:end
pause
