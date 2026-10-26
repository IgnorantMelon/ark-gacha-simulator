$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
Push-Location -LiteralPath $ProjectRoot
try {
    uv run --locked ark-gacha sync
    if ($LASTEXITCODE -ne 0) { throw 'Data sync failed. Previous published version is preserved.' }
    uv run --locked ark-gacha validate
    if ($LASTEXITCODE -ne 0) { throw 'Data validation failed.' }
} finally { Pop-Location }
