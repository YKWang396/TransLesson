$ErrorActionPreference = 'Stop'
# Explicit setup only. Runtime never fetches packages, fonts or TeX components.
Push-Location $PSScriptRoot
try {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        npm install --ignore-scripts --no-audit --no-fund
    } elseif (Get-Command pnpm -ErrorAction SilentlyContinue) {
        pnpm install --ignore-scripts
    } else {
        throw 'Install Node.js with npm, then run this setup script again.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'MathJax installation failed.' }
} finally {
    Pop-Location
}
