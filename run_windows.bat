@echo off
cd /d "%~dp0"

where python > nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python is not installed. Install it from https://www.python.org/downloads/
  echo         Check "Add python.exe to PATH" during installation.
  pause
  exit /b 1
)

if not exist .venv (
  echo First run: installing required packages...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
python -m pip install -q --upgrade "yt-dlp[default]"

python app.py
pause
