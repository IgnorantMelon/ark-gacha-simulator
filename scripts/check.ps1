$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
Push-Location -LiteralPath $ProjectRoot
try {
    uv run --locked ruff check .
    if ($LASTEXITCODE -ne 0) { throw 'Ruff check failed.' }
    uv run --locked ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw 'Format check failed.' }
    uv run --locked pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
} finally { Pop-Location }
