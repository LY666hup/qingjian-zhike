@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0launch.py" %*
  goto finished
)
for %%D in ("%~dp0.." "%~dp0..\.." "%~dp0..\..\.." "%~dp0..\..\..\.." "%~dp0..\..\..\..\..") do (
  if exist "%%~fD\wmvenv\Scripts\python.exe" (
    "%%~fD\wmvenv\Scripts\python.exe" -X utf8 "%~dp0launch.py" %*
    goto finished
  )
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -X utf8 "%~dp0launch.py" %*
  goto finished
)
where python >nul 2>nul
if not errorlevel 1 (
  python -X utf8 "%~dp0launch.py" %*
  goto finished
)
echo 未找到 Python 3.10 或以上版本。请安装 Python 和 requirements.txt 中的依赖。
pause
exit /b 2
:finished
if errorlevel 1 pause
endlocal
