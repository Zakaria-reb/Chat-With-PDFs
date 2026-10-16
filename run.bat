@echo off
cd /d "%~dp0"
python -m uvicorn app.main:app --port 8000
pause
