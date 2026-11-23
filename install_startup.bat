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
set "AGENTMON_ROOT=%~dp0"
set TASK_RESULT=FAILED
set DASH_RESULT=FAILED

echo [1/4] Stopping existing dashboard...
schtasks /end /tn "%TASK_NAME%" >nul 2>&1
powershell -NoProfile -Command "$root = $env:AGENTMON_ROOT.TrimEnd('\'); Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*dashboard.py*' -and $_.ExecutablePath -eq ($root + '\.venv\Scripts\python.exe') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 1 >nul
echo [1/4] Existing dashboard stopped.

echo [2/4] Registering scheduled task...
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "& { $vbs = $env:AGENTMON_VBS; $a = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument ('""' + $vbs + '""'); $t = New-ScheduledTaskTrigger -AtLogOn; $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero); Register-ScheduledTask -TaskName 'agentmon' -Action $a -Trigger $t -Settings $s -RunLevel Highest -Force | Out-Null }"
schtasks /query /tn "%TASK_NAME%" >nul 2>&1
if not errorlevel 1 set TASK_RESULT=OK
echo [2/4] Task registration: %TASK_RESULT%

echo [3/4] Starting dashboard...
wscript.exe "%AGENTMON_VBS%"
timeout /t 4 >nul

echo [4/4] Checking dashboard...
powershell -NoProfile -Command "$root = $env:AGENTMON_ROOT.TrimEnd('\'); if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*dashboard.py*' -and $_.ExecutablePath -eq ($root + '\.venv\Scripts\python.exe') }) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    set DASH_RESULT=OK
) else (
    echo [4/4] wscript failed - starting python directly...
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
