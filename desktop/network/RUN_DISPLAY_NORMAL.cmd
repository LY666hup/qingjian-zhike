@echo off
cd /d "%~dp0"
if exist "%~dp0..\current\.venv\Scripts\python.exe" (
  "%~dp0..\current\.venv\Scripts\python.exe" -X utf8 "%~dp0normal_display.py" %*
) else (
  py -3 -X utf8 "%~dp0normal_display.py" %*
)
if errorlevel 1 pause
