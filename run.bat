@echo off
cd /d "%~dp0"
echo ============================================
echo  驾驶员分心驾驶行为检测系统
echo ============================================
echo.
echo 正在启动，请稍候...
echo.
call venv\Scripts\activate.bat
python main.py
pause
