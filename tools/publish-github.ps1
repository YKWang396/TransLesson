param([string]$Owner = 'YKWang396', [string]$Repository = 'MathBridge')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
if ($Owner -notmatch '^[A-Za-z0-9-]+$' -or $Repository -notmatch '^[A-Za-z0-9_.-]+$') { throw 'Invalid GitHub repository name.' }
if (git status --porcelain) { throw 'Commit the reviewed changes before publishing.' }
if ((git branch --show-current) -ne 'main') { throw 'Switch to the reviewed main branch before publishing.' }
# Use Git Credential Manager. Credentials remain in memory and are never logged
# or written into this repository. Browser login alone is not a Git credential.
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'never'
$credentialLines = "protocol=https`nhost=github.com`nusername=$Owner`n`n" | git credential fill 2>$null
if ($LASTEXITCODE -ne 0) { throw 'GitHub Git credentials unavailable. Run git credential-manager github login --browser first.' }
$credential = @{}
foreach ($entry in $credentialLines) {
    $parts = $entry -split '=', 2
    if ($parts.Length -eq 2) { $credential[$parts[0]] = $parts[1] }
}
if (-not $credential['password']) { throw 'GitHub credential is missing.' }
$headers = @{ Authorization = 'Bearer ' + $credential['password']; Accept = 'application/vnd.github+json'; 'X-GitHub-Api-Version' = '2022-11-28' }
try {
    $account = Invoke-RestMethod -Uri 'https://api.github.com/user' -Headers $headers -TimeoutSec 30
    if ($account.login -ine $Owner) { throw 'Signed-in GitHub account differs from the requested owner.' }
    $repoUrl = "https://api.github.com/repos/$Owner/$Repository"
    try { $repo = Invoke-RestMethod -Uri $repoUrl -Headers $headers -TimeoutSec 30 }
    catch {
        if ([int]$_.Exception.Response.StatusCode -ne 404) { throw }
        $body = @{ name = $Repository; private = $true; description = 'Local mathematics lecture translation and review desktop app'; auto_init = $false } | ConvertTo-Json
        $repo = Invoke-RestMethod -Method Post -Uri 'https://api.github.com/user/repos' -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 30
    }
    if (-not $repo.private -or $repo.owner.login -ine $Owner) { throw 'Refusing to publish: expected a private repository owned by the requested account.' }
    $expected = "https://github.com/$Owner/$Repository.git"
    $origin = git remote get-url origin 2>$null
    if ($LASTEXITCODE -ne 0) { git remote add origin $expected }
    elseif ($origin.TrimEnd('/') -ine $expected) { throw 'Existing origin differs from the requested GitHub repository.' }
    if ($LASTEXITCODE -ne 0) { throw 'Unable to configure Git remote.' }
    git push --set-upstream origin main
    if ($LASTEXITCODE -ne 0) { throw 'GitHub main push failed.' }
    git push origin --tags
    if ($LASTEXITCODE -ne 0) { throw 'GitHub tag push failed.' }
    $localHead = git rev-parse HEAD
    $remoteLine = git ls-remote --heads origin main
    if ($LASTEXITCODE -ne 0 -or -not $remoteLine -or -not $remoteLine.StartsWith($localHead)) { throw 'Remote revision verification failed.' }
    @{ repository = $repo.html_url; private = $repo.private; revision = $localHead; verified = $true } | ConvertTo-Json
}
finally {
    $headers.Clear()
    $credential.Clear()
    $credentialLines = $null
}
