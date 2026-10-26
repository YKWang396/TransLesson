@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
  echo Please run Setup-MathBridge.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m mathbridge.app %*
if errorlevel 1 pause
