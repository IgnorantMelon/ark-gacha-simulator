$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
Push-Location -LiteralPath $ProjectRoot
try {
    if (-not (Test-Path -LiteralPath '.env')) { throw 'Run scripts/setup.ps1 first.' }
    uv run --locked ark-gacha run
    if ($LASTEXITCODE -ne 0) { throw 'Bot stopped with an error.' }
} finally { Pop-Location }
