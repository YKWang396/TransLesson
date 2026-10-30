@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0gpt-sovits-service.ps1" -BundleRoot "%~dp0GPT-SoVITS" -Action stop
pause
