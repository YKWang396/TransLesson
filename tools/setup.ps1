param([switch]$WithASR, [switch]$WithSpeakers)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$launcherCommand = Get-Command py -ErrorAction SilentlyContinue
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if ($launcherCommand) { & $launcherCommand.Source -3 -m venv .venv }
    elseif ($pythonCommand) { & $pythonCommand.Source -m venv .venv }
    elseif (Test-Path -LiteralPath $bundledPython) { & $bundledPython -m venv .venv }
    else { throw 'Install Python 3.11 or 3.12 x64, then run setup again.' }
    if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed.' }
}
$package = if ($WithASR -and $WithSpeakers) { '.[asr,diarization]' }
    elseif ($WithASR) { '.[asr]' }
    elseif ($WithSpeakers) { '.[diarization]' }
    else { '.' }
& '.\.venv\Scripts\python.exe' -m pip install -e $package
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$pnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue
$npmCommand = Get-Command npm -ErrorAction SilentlyContinue
$bundledNode = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$bundledPnpm = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\pnpm\bin\pnpm.cjs'
Push-Location -LiteralPath 'src\mathbridge\handout'
try {
    if ($pnpmCommand) { & $pnpmCommand.Source install --frozen-lockfile --ignore-scripts }
    elseif ($npmCommand) { & $npmCommand.Source install --ignore-scripts }
    elseif ((Test-Path -LiteralPath $bundledNode) -and (Test-Path -LiteralPath $bundledPnpm)) { & $bundledNode $bundledPnpm install --frozen-lockfile --ignore-scripts --store-dir '.pnpm-store' }
    else { throw 'Install Node.js LTS and npm for local MathJax formula rendering.' }
    if ($LASTEXITCODE -ne 0) { throw 'MathJax dependency installation failed.' }
}
finally { Pop-Location }
Write-Host 'TransLesson development environment is ready. Run tools/run-dev.ps1. ASR weights are imported separately; no course data has been uploaded.'
