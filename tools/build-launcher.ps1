$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Windows .NET Framework C# compiler is required to build the workspace launcher.' }
$source = Join-Path $projectRoot 'packaging\windows\WorkspaceLauncher.cs'
$target = Join-Path $projectRoot '课译TransLesson.exe'
$icon = Join-Path $projectRoot 'src\mathbridge\resources\translesson.ico'
& $compiler /nologo /codepage:65001 /target:winexe /platform:x64 /optimize+ "/win32icon:$icon" "/out:$target" $source
if ($LASTEXITCODE -ne 0) { throw 'Workspace launcher build failed.' }
# The outer workspace also gets an entry point when it contains this checkout.
$workspace = Split-Path $projectRoot -Parent
if ((Split-Path $projectRoot -Leaf) -eq 'MathBridge') {
    Copy-Item -LiteralPath $target -Destination (Join-Path $workspace '课译TransLesson.exe') -Force
}
Write-Host "Ready: $target"
