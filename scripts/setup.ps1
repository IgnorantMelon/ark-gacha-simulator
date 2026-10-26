$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
Push-Location -LiteralPath $ProjectRoot
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'
    }
    uv sync --locked --python 3.11
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    if (-not (Test-Path -LiteralPath '.env')) {
        $RandomBytes = New-Object byte[] 32
        $Generator = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $Generator.GetBytes($RandomBytes) } finally { $Generator.Dispose() }
        $TokenValue = [BitConverter]::ToString($RandomBytes).Replace('-', '').ToLowerInvariant()
        $ConfigText = [IO.File]::ReadAllText((Join-Path $ProjectRoot '.env.example'))
        $ConfigText = $ConfigText.Replace('ARK_ONEBOT_TOKEN=', "ARK_ONEBOT_TOKEN=$TokenValue")
        [IO.File]::WriteAllText((Join-Path $ProjectRoot '.env'), $ConfigText,
            (New-Object Text.UTF8Encoding($false)))
        Write-Host 'Created .env with a random OneBot token. Configure admin QQ and allowed groups.'
    }
    Write-Host 'Ready. Next: uv run ark-gacha sync; configure NapCat; run scripts/start.ps1.'
} finally { Pop-Location }
