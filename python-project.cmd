@echo off
setlocal
set PYTHONNOUSERSITE=1
"%~dp0.venv\Scripts\python.exe" %*
