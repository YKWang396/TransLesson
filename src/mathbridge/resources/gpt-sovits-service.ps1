param(
    [Parameter(Mandatory=$true)][string]$BundleRoot,
    [ValidateSet('start','stop')][string]$Action = 'start',
    [ValidateSet('127.0.0.1','::1')][string]$Address = '127.0.0.1',
    [ValidateRange(1,65535)][int]$Port = 9880
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $bundle = (Resolve-Path -LiteralPath $BundleRoot).Path
    $python = Join-Path $bundle 'runtime\python.exe'
    $api = Join-Path $bundle 'api_v2.py'
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $api)) { throw 'GPT-SoVITS directory is incomplete.' }
    $config = 'GPT_SoVITS/configs/tts_translesson.yaml'
    if (-not (Test-Path -LiteralPath (Join-Path $bundle $config))) { $config = 'GPT_SoVITS/configs/tts_infer.yaml' }
    if (-not (Test-Path -LiteralPath (Join-Path $bundle $config))) { throw 'GPT-SoVITS inference configuration is missing.' }
    $logs = Join-Path $bundle 'output'
    $null = New-Item -ItemType Directory -Path $logs -Force
    $stateFile = Join-Path $logs ("translesson-api-{0}.json" -f $Port)
    $service = $null
    if (Test-Path -LiteralPath $stateFile) {
        $state = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $candidate = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$state.pid)" -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.ExecutablePath -eq $python -and
            $candidate.CommandLine -match '(?:^|\s)api_v2\.py(?:\s|$)' -and
            $candidate.CommandLine -match ("(?:^|\s)-p\s+{0}(?:\s|$)" -f $Port) -and
            $candidate.CommandLine -match ("(?:^|\s)-a\s+{0}(?:\s|$)" -f [regex]::Escape($Address))) {
            $service = $candidate
        }
    }
    if ($Action -eq 'stop') {
        if ($service) { Stop-Process -Id $service.ProcessId -ErrorAction Stop }
        @{status= $(if ($service) {'stopped'} else {'not_owned'});port=$Port} | ConvertTo-Json -Compress
        exit 0
    }
    if ($service) {
        @{status='starting';pid=$service.ProcessId;port=$Port;logs=$logs} | ConvertTo-Json -Compress
        exit 0
    }
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        @{status='external';port=$Port;logs=$logs} | ConvertTo-Json -Compress
        exit 0
    }
    $env:PATH = (Join-Path $bundle 'runtime') + ';' + $env:PATH
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUNBUFFERED = '1'
    $env:HF_HUB_OFFLINE = '1'
    $env:TRANSFORMERS_OFFLINE = '1'
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $stdout = Join-Path $logs "translesson-api-$stamp.stdout.log"
    $stderr = Join-Path $logs "translesson-api-$stamp.stderr.log"
    $process = Start-Process -FilePath $python -ArgumentList @('-I','-u','api_v2.py','-a',$Address,'-p',"$Port",'-c',$config) -WorkingDirectory $bundle -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    $value = @{status='starting';pid=$process.Id;port=$Port;address=$Address;logs=$logs;stdout=$stdout;stderr=$stderr;config=$config}
    $temporary = $stateFile + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    $value | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $stateFile -Force
    $value | ConvertTo-Json -Compress
}
catch {
    @{status='error';message=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 1
}
