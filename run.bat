@echo off
title SARA Backend Server
cd /d "%~dp0"
echo ==============================================
echo        SARA Companion Server Starting...
echo ==============================================
echo.
"venv\Scripts\python.exe" main.py
pause
