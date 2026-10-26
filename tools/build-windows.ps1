$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm 'packaging/windows/MathBridge.spec'
if ($LASTEXITCODE -ne 0) { throw 'Windows build failed.' }
& '.\.venv\Scripts\python.exe' 'tools/runtime-report.py'
if ($LASTEXITCODE -ne 0) { throw 'Runtime manifest generation failed.' }
Write-Host 'Portable application: dist\MathBridge\MathBridge.exe. Keep the whole folder together.'
