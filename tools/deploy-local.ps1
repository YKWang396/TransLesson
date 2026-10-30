param([string]$InstallRoot = (Join-Path ([Environment]::GetFolderPath('Desktop')) '课译TransLesson'))
$ErrorActionPreference = 'Stop'
$repository = Split-Path $PSScriptRoot -Parent
$payload = Join-Path $repository 'dist\TransLesson'
$executable = Join-Path ([IO.Path]::GetFullPath($InstallRoot)) '课译TransLesson.exe'
if (-not (Test-Path -LiteralPath (Join-Path $payload '课译TransLesson.exe'))) { throw 'Build the Windows application first.' }
$active = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $executable })
if ($active.Count) { throw 'Close the installed application before updating it.' }
$null = New-Item -ItemType Directory -Path $InstallRoot -Force
$log = Join-Path $repository 'artifacts\deploy-local.log'
$null = New-Item -ItemType Directory -Path (Split-Path $log -Parent) -Force
# /E copies the application and helper scripts without deleting independently
# installed component folders or user-created files in the destination.
& robocopy.exe $payload $InstallRoot /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /MT:8 /XJ /NFL /NDL /NP "/LOG:$log"
if ($LASTEXITCODE -ge 8) { throw "Application copy failed; see $log" }
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $desktop '课译TransLesson.lnk'))
$shortcut.TargetPath = $executable
$shortcut.WorkingDirectory = Split-Path $executable -Parent
$shortcut.IconLocation = $executable + ',0'
$shortcut.Description = '课程翻译工作台'
$shortcut.Save()
Write-Output "Installed: $executable"
exit 0
