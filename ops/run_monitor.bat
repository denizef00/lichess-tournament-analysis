@echo off
rem Task Scheduler entry point: run the monitor from the repository root
cd /d "%~dp0.."
set PYTHONUNBUFFERED=1
if not exist logs mkdir logs
echo --- %date% %time% --- >> logs\monitor.log
".venv\Scripts\python.exe" src\monitor.py >> logs\monitor.log 2>&1
exit /b %ERRORLEVEL%
