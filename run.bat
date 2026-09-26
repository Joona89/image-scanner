@echo off
rem First run creates a virtual environment and installs dependencies.
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw -m scanner_app %*
