@echo off
REM B 站 Cookie 设置界面（双击运行）
REM
REM 为什么不直接双击 .py：那样会用系统默认的 python 打开，
REM 而 tkinter 与依赖都装在项目的 .venv 里，可能报「模块找不到」。
REM 这里显式用 .venv 的解释器。
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "bilibili_downloader\cookie_gui.py"
) else (
    echo 未找到 .venv\Scripts\pythonw.exe
    echo 请先确认虚拟环境已建好（见 README 的「安装」一节）。
    pause
)
