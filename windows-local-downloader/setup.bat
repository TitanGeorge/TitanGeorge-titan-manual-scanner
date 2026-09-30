@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import sys; assert sys.version_info >= (3,11)" >nul 2>&1
if errorlevel 1 (
  echo Install Python 3.11 or newer with the Python launcher from https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
py -3 -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
.venv\Scripts\python.exe downloader.py init
if errorlevel 1 goto fail
echo Setup complete. No Service Alliance requests or PDF downloads were made.
pause
exit /b 0
:fail
echo Setup failed. Review the message above.
pause
exit /b 1
