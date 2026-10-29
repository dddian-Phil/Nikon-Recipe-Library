param([switch]$PrepareOnly, [string]$SourceWorkspace)
$ErrorActionPreference = 'Stop'
try {
    $workspacePath = Split-Path -Parent $PSScriptRoot
    $deploymentPath = Join-Path $workspacePath 'local\worktrees\static-deploy'
    if (Test-Path -LiteralPath (Join-Path $workspacePath 'app\publish_site.py')) {
        $deploymentPath = $workspacePath
        $candidateWorkspace = [IO.Path]::GetFullPath((Join-Path $workspacePath '..\..\..'))
        $expectedDeployment = Join-Path $candidateWorkspace 'local\worktrees\static-deploy'
        if ($expectedDeployment -eq $deploymentPath) { $workspacePath = $candidateWorkspace }
    }
    if ($SourceWorkspace) { $workspacePath = (Resolve-Path -LiteralPath $SourceWorkspace).Path }
    if (-not (Test-Path -LiteralPath (Join-Path $deploymentPath 'app\publish_site.py'))) {
        throw 'Deployment worktree is missing. See docs/deployment.md in codex/static-deploy.'
    }
    $pythonPath = 'python'
    $configPath = Join-Path $workspacePath 'local\runtime.json'
    if (Test-Path -LiteralPath $configPath) {
        $runtimeConfig = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        if ($runtimeConfig.python) { $pythonPath = $runtimeConfig.python }
    }
    $publishArgs = @('-B', (Join-Path $deploymentPath 'app\publish_site.py'), '--workspace', $workspacePath)
    if ($PrepareOnly) { $publishArgs += '--prepare-only' }
    & $pythonPath @publishArgs
    exit $LASTEXITCODE
} catch {
    Write-Host ('Website sync failed: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
