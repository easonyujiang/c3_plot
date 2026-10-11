@echo off
setlocal
cd /d "%~dp0"
title C3-Mass Performance Dashboard

echo.
echo C3-Mass Dashboard
echo Dashboard: http://localhost:8765
echo.

if exist ".venv\Scripts\python.exe" goto use_venv

py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 goto use_py

if exist "C:\ProgramData\anaconda3\python.exe" goto use_conda

python -c "import sys" >nul 2>&1
if not errorlevel 1 goto use_python

echo [ERROR] Python interpreter not found.
echo Add Python to PATH or create .venv in this folder.
pause
exit /b 1

:use_venv
echo Using .venv Python
".venv\Scripts\python.exe" run.py
goto done

:use_py
echo Using Python launcher
py -3 run.py
goto done

:use_conda
echo Using Anaconda Python
"C:\ProgramData\anaconda3\python.exe" run.py
goto done

:use_python
echo Using system Python
python run.py

:done
echo.
echo C3-Mass Dashboard stopped.
pause
