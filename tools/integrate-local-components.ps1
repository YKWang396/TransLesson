param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [Parameter(Mandatory=$true)][string]$GptSource,
    [Parameter(Mandatory=$true)][string]$WhisperSource,
    [switch]$Copy
)
$ErrorActionPreference = 'Stop'
$install = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$components = Join-Path $install 'components'
$null = New-Item -ItemType Directory -Path $components -Force
foreach ($item in @(@{source=$GptSource;name='GPT-SoVITS'},@{source=$WhisperSource;name='faster-whisper-small'})) {
    $target = [IO.Path]::GetFullPath((Join-Path $components $item.name))
    if (-not $target.StartsWith($components + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Destination must remain inside the selected installation components folder.' }
    if (-not (Test-Path -LiteralPath $item.source)) {
        if (Test-Path -LiteralPath $target) { Write-Output "Already integrated: $target"; continue }
        throw "Component source is missing: $($item.source)"
    }
    $source = (Resolve-Path -LiteralPath $item.source).Path.TrimEnd('\')
    if ($source -eq $target) { continue }
    if (Test-Path -LiteralPath $target) { throw "Destination already exists; refusing to overwrite: $target" }
    $entry=Get-Item -LiteralPath $source
    if (-not $entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Expected an ordinary component directory.' }
    if ($item.name -eq 'GPT-SoVITS') {
        $python=Join-Path $source 'runtime\python.exe'
        if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath (Join-Path $source 'api_v2.py'))) { throw 'GPT-SoVITS bundle is incomplete.' }
        $processes=@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $python })
        foreach ($process in $processes) {
            if ($process.CommandLine -notmatch '(?:^|\s)api_v2\.py(?:\s|$)') { throw 'Close other GPT-SoVITS tools before relocating this bundle.' }
        }
        foreach ($process in $processes) {
            Stop-Process -Id $process.ProcessId -ErrorAction Stop
            Wait-Process -Id $process.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        }
    } elseif (-not (Test-Path -LiteralPath (Join-Path $source 'model.bin'))) { throw 'Whisper model.bin is missing.' }
    # Both resolved paths were checked above; move only the explicitly selected
    # component root. No content is deleted or recursively merged/overwritten.
    if ($Copy) {
        $log = Join-Path $components ("copy-{0}.log" -f $item.name)
        & robocopy.exe $source $target /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /MT:12 /XJ /NFL /NDL /NP "/LOG:$log"
        if ($LASTEXITCODE -ge 8) { throw "Component copy failed; see $log" }
    } else { Move-Item -LiteralPath $source -Destination $target }
    Write-Output "Integrated: $target"
}
exit 0
