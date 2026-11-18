@echo off
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    echo Run as Administrator (right-click - Run as administrator)
    pause
    exit /b 1
)

set TASK_NAME=agentmon
set VBS=%~dp0run_silent.vbs

schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
schtasks /create /tn "%TASK_NAME%" /tr "wscript.exe \"%VBS%\"" /sc onlogon /rl highest /f

if errorlevel 1 (
    echo Registration failed
    pause
    exit /b 1
)

echo Registered: %TASK_NAME%
echo Auto-starts on next login (no window).
echo.
echo Press any key to start now...
pause >nul
schtasks /run /tn "%TASK_NAME%"
echo Done. Check Ulanzi display.
pause
