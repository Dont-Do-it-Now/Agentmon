@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating venv...
    python -m venv .venv
)

.venv\Scripts\pip install -q -r requirements.txt

echo Starting agentmon...
.venv\Scripts\python.exe dashboard.py
if errorlevel 1 (
    echo Error occurred: %errorlevel%
)
pause
