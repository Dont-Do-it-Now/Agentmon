@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
".venv\Scripts\python.exe" dashboard.py > "logs\dashboard.log" 2>&1
