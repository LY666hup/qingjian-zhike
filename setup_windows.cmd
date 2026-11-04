@echo off
cd /d "%~dp0"
py -3 -m venv desktop\current\.venv
if errorlevel 1 exit /b 1
desktop\current\.venv\Scripts\python.exe -m pip install -r environment\windows.txt -r environment\windows-integration.txt
