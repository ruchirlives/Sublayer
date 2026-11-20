param(
    [switch]$NoLastProject,
    [string]$Project = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildRoot = 'I:/AI/OrchFlow-build'
$exe = Join-Path $buildRoot 'debug/Sublayer.exe'

$appProcessNames = @('sublayer-updated', 'orchflow-updated', 'orchflow-dragfix', 'tauri-app', 'Sublayer')
$running = Get-Process -Name $appProcessNames -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "Stopping running Sublayer app process(es): $(($running | Select-Object -ExpandProperty ProcessName -Unique) -join ', ')"
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    $running | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
}

$runtimeRoot = if ($env:ORCHFLOW_USD_ROOT) { $env:ORCHFLOW_USD_ROOT } else { 'I:\AI\OpenUSD-runtime\25.08' }
if (-not $env:ORCHFLOW_PYTHON) {
    $imagingPython = Join-Path $projectRoot '.venv-imaging\Scripts\python.exe'
    if ((Test-Path -LiteralPath $imagingPython) -and (Test-Path -LiteralPath (Join-Path $runtimeRoot 'lib\python\pxr\UsdImagingGL'))) {
        $env:ORCHFLOW_PYTHON = $imagingPython
        $env:ORCHFLOW_USD_ROOT = $runtimeRoot
    } else {
        $env:ORCHFLOW_PYTHON = Join-Path $projectRoot '.venv\Scripts\python.exe'
        Write-Warning 'Using core-only inspection runtime. Run scripts/setup-imaging.ps1 for Hydra rendering.'
    }
}
if (-not (Test-Path -LiteralPath $env:ORCHFLOW_PYTHON)) { throw 'Create a runtime using README.md or scripts/setup-imaging.ps1 first.' }
if (-not $env:ORCHFLOW_OVRTX_PYTHON) {
    $ovrtxPython = Join-Path $projectRoot '.venv-ovrtx\Scripts\python.exe'
    if (Test-Path -LiteralPath $ovrtxPython) {
        $env:ORCHFLOW_OVRTX_PYTHON = $ovrtxPython
    }
}

& (Join-Path $PSScriptRoot 'build.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }

if (-not (Test-Path $exe)) {
    throw "Built executable not found at $exe"
}

$launchArgs = @()
if ($Project) {
    $launchArgs += @('--project', $Project)
} elseif (-not $NoLastProject) {
    $launchArgs += '--last-project'
}

if ($launchArgs.Count -gt 0) {
    Start-Process -FilePath $exe -ArgumentList $launchArgs -WorkingDirectory $projectRoot
} else {
    Start-Process -FilePath $exe -WorkingDirectory $projectRoot
}
