$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm 'packaging/windows/TransLesson.spec'
if ($LASTEXITCODE -ne 0) { throw 'Windows build failed.' }
& '.\.venv\Scripts\python.exe' 'tools/runtime-report.py'
if ($LASTEXITCODE -ne 0) { throw 'Runtime manifest generation failed.' }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'tools/build-launcher.ps1'
if ($LASTEXITCODE -ne 0) { throw 'Workspace launcher build failed.' }
Write-Host 'Portable application: dist\TransLesson\课译TransLesson.exe. Keep the whole folder together.'
