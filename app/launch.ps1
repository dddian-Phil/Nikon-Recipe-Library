param([ValidateSet('open','update')][string]$Action = 'open')
$ErrorActionPreference = 'Stop'
$programPath = $PSScriptRoot
$workspacePath = Split-Path -Parent $programPath
$localPath = Join-Path $workspacePath 'local'
New-Item -ItemType Directory -Path $localPath -Force | Out-Null
$configPath = Join-Path $localPath 'runtime.json'
if (-not (Test-Path -LiteralPath $configPath)) { $configPath = Join-Path $programPath 'runtime.example.json' }
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$pythonPath = $config.python
if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = (Get-Command python -ErrorAction Stop).Source }
$portNumber = [int]$config.port
$serviceUrl = 'http://127.0.0.1:' + $portNumber
$workspaceHasher = [System.Security.Cryptography.SHA256]::Create()
$expectedWorkspace = ([System.BitConverter]::ToString($workspaceHasher.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($workspacePath)))).Replace('-','').ToLowerInvariant()

function Get-LibraryHealth {
    try {
        $response = Invoke-RestMethod -Uri ($serviceUrl + '/api/health') -TimeoutSec 2
        if ($response.service -eq 'nikon-local-library' -and $response.workspace -eq $expectedWorkspace) { return $true }
        throw 'The configured local port belongs to another application.'
    } catch [System.Net.WebException] { return $false }
}

try {
    if (-not (Get-LibraryHealth)) {
        $serverPath = Join-Path $programPath 'library_server.py'
        $logPath = Join-Path $localPath 'runtime.log'
        if (-not (Test-Path -LiteralPath (Join-Path $programPath 'viewer.html'))) {
            & $pythonPath (Join-Path $programPath 'build_viewer.py')
            if ($LASTEXITCODE -ne 0) { throw 'Viewer generation failed. See local/runtime.log.' }
        }
        Start-Process -FilePath $pythonPath -ArgumentList @(('"' + $serverPath + '"'),'--port',"$portNumber") -WorkingDirectory $workspacePath -WindowStyle Hidden -RedirectStandardError $logPath | Out-Null
        $ready = $false
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            Start-Sleep -Milliseconds 250
            if (Get-LibraryHealth) { $ready = $true; break }
        }
        if (-not $ready) { throw 'Library startup failed. See local/runtime.log or guide.html.' }
    }
    if ($Action -eq 'update') {
        Invoke-RestMethod -Uri ($serviceUrl + '/api/scan') -Method Post -ContentType 'application/json' -Body '{}' | Out-Null
        for ($attempt = 0; $attempt -lt 300; $attempt++) {
            Start-Sleep -Milliseconds 300
            $state = Invoke-RestMethod -Uri ($serviceUrl + '/api/state') -TimeoutSec 5
            if (-not $state.scanning) { break }
        }
        if ($state.scanError) { throw $state.scanError }
        Write-Host ('Photos: ' + $state.summary.photos + '; files: ' + $state.summary.files + '; linked: ' + $state.summary.matched)
    } else {
        Start-Process ($serviceUrl + '/') | Out-Null
    }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
