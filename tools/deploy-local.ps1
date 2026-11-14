param([string]$InstallRoot = (Join-Path ([Environment]::GetFolderPath('Desktop')) '课译TransLesson'), [switch]$SkipShortcut)
$ErrorActionPreference = 'Stop'
$repository = Split-Path $PSScriptRoot -Parent
$payload = Join-Path $repository 'dist\TransLesson'
$executable = Join-Path ([IO.Path]::GetFullPath($InstallRoot)) '课译TransLesson.exe'
if (-not (Test-Path -LiteralPath (Join-Path $payload '课译TransLesson.exe'))) { throw 'Build the Windows application first.' }
$active = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $executable })
if ($active.Count) { throw 'Close the installed application before updating it.' }
$previousDocs = @()
$previousManifest = Join-Path $InstallRoot 'runtime-manifest.json'
if (Test-Path -LiteralPath $previousManifest) {
    $previousDocs = @((Get-Content -LiteralPath $previousManifest -Raw -Encoding UTF8 | ConvertFrom-Json).documentation_files)
}
$null = New-Item -ItemType Directory -Path $InstallRoot -Force
$log = Join-Path $repository 'artifacts\deploy-local.log'
$null = New-Item -ItemType Directory -Path (Split-Path $log -Parent) -Force
# /E copies the application and helper scripts without deleting independently
# installed component folders or user-created files in the destination.
& robocopy.exe $payload $InstallRoot /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /MT:8 /XJ /NFL /NDL /NP "/LOG:$log"
if ($LASTEXITCODE -ge 8) { throw "Application copy failed; see $log" }
# Remove only app-managed documentation retired by the new package. Keep user
# files and independently installed components, including custom documentation.
$legacyDocs = @('04-delivery-plan.md','06-sources.md','07-api-strategy.md','09-implementation-status.md',
    '10-validation-report.md','13-ai-course-workflow.md','14-adaptive-dubbing.md','15-translesson-launch.md',
    '16-auto-voice-reference.md','17-sentence-timing-voice.md','18-components-and-branding.md','19-speech-content-check.md')
$retired = @($previousDocs) + @($legacyDocs | ForEach-Object { 'docs/' + $_ })
$docsRoot = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'docs')) + [IO.Path]::DirectorySeparatorChar
foreach ($relative in $retired) {
    if (-not $relative -or (Test-Path -LiteralPath (Join-Path $payload $relative))) { continue }
    $target = [IO.Path]::GetFullPath((Join-Path $InstallRoot $relative))
    if (-not $target.StartsWith($docsRoot, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetExtension($target) -ne '.md') { throw 'Invalid managed documentation path.' }
    if (Test-Path -LiteralPath $target -PathType Leaf) { Remove-Item -LiteralPath $target -Force }
}
if (-not $SkipShortcut) {
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $desktop '课译TransLesson.lnk'))
$shortcut.TargetPath = $executable
$shortcut.WorkingDirectory = Split-Path $executable -Parent
$shortcut.IconLocation = $executable + ',0'
$shortcut.Description = '课程翻译工作台'
$shortcut.Save()
}
Write-Output "Installed: $executable"
exit 0
