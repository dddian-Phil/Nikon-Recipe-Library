param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
try {
    $restoreRoot = Split-Path -Parent $PSScriptRoot
    $pythonPath = 'python'
    $configPath = Join-Path $restoreRoot 'local\runtime.json'
    if (Test-Path -LiteralPath $configPath) {
        $restoreConfig = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($restoreConfig.python) { $pythonPath = $restoreConfig.python }
    }
    $restoreArgs = @((Join-Path $PSScriptRoot 'restore_v1.py'))
    if (-not $CheckOnly) { $restoreArgs += '--restore' }
    & $pythonPath @restoreArgs
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
