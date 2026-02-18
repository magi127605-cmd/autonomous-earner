@echo off
chcp 65001 >nul
echo ================================
echo  Autonomous Earner - Starting...
echo ================================
cd /d "%~dp0"
pythonw daemon.py
echo Daemon started in background.
echo To stop: change ENABLED=false in .env
pause
