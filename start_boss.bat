@echo off
cd /d "%~dp0"
echo ====================================
echo  👔 老板驾驶监控面板
echo ====================================
echo.
echo  先确保 server.py 已在运行！
echo  如果没启动，请另开窗口运行:
echo     venv\Scripts\python server.py
echo.
echo  正在启动老板端...
start "BossPanel" /B venv\Scripts\python boss_app.py
echo  老板端已启动！
echo.
pause
