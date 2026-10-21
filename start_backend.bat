@echo off
cd /d "%~dp0"
call ..\.venv\Scripts\activate.bat
uvicorn backend.main:app --reload --port 8000
pause
