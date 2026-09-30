@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe downloader.py resume
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
