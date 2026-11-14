$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
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
if (-not $env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR = 'I:\AI\OrchFlow-build' }
Write-Host 'Development mode: frontend edits hot-reload; Rust changes rebuild/restart automatically.'
Push-Location $projectRoot
try { npm.cmd run tauri -- dev --config (Join-Path $projectRoot 'src-tauri\tauri.dev.conf.json') } finally { Pop-Location }
