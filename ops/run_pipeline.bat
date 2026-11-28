@echo off
rem Task Scheduler entry point: run the pipeline from the repository root
cd /d "%~dp0.."
set PYTHONUNBUFFERED=1
if not exist logs mkdir logs
echo --- %date% %time% --- >> logs\pipeline.log
".venv\Scripts\python.exe" src\run_pipeline.py >> logs\pipeline.log 2>&1
exit /b %ERRORLEVEL%
