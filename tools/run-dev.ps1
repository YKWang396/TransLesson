$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONUTF8 = '1'
& '.\.venv\Scripts\python.exe' -m mathbridge.app @args
exit $LASTEXITCODE
