@echo off
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    echo ERROR: Run as Administrator ^(right-click - Run as administrator^)
    pause
    exit /b 1
)

set TASK_NAME=agentmon
set "AGENTMON_VBS=%~dp0run_silent.vbs"
set TASK_RESULT=FAILED
set DASH_RESULT=FAILED

echo [1/3] Registering scheduled task...
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { $vbs = $env:AGENTMON_VBS; $a = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('""' + $vbs + '""'); $t = New-ScheduledTaskTrigger -AtLogOn; $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero); Register-ScheduledTask -TaskName 'agentmon' -Action $a -Trigger $t -Settings $s -RunLevel Highest -Force | Out-Null }"
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if not errorlevel 1 set TASK_RESULT=OK
echo [1/3] Task registration: %TASK_RESULT%

echo [2/3] Starting dashboard...
wscript.exe "%AGENTMON_VBS%"
timeout /t 4 >nul

echo [3/3] Checking dashboard...
powershell -NoProfile -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*dashboard*' }) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    set DASH_RESULT=OK
) else (
    echo [3/3] wscript failed - starting python directly...
    start "" "%~dp0.venv\Scripts\python.exe" "%~dp0dashboard.py"
    timeout /t 3 >nul
    set DASH_RESULT=OK^(direct^)
)

echo.
echo ========================================
echo  Task registration : %TASK_RESULT%
echo  Dashboard running : %DASH_RESULT%
echo ========================================
pause