@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [!] 未找到虚拟环境 .venv
    echo     请先执行：
    echo         py -3.13 -m venv .venv
    echo         .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo 启动 PRTSBox...
echo 日志：data\logs\prtsbox.log
echo.
echo 关闭此窗口不会退出程序；退出程序请关掉主界面窗口。
echo.

REM pythonw keeps the console from lingering; run.py writes logs to data\logs.
start "" ".venv\Scripts\pythonw.exe" run.py
