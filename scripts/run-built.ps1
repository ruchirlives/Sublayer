$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
# Prefer the newest successful build; a replacement can be built while the old app is open.
$executable = @('I:\AI\OrchFlow-build\debug\sublayer-updated.exe', 'I:\AI\OrchFlow-build\debug\orchflow-updated.exe', 'I:\AI\OrchFlow-build\debug\orchflow-dragfix.exe') |
    Where-Object { Test-Path -LiteralPath $_ } |
    Get-Item | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $executable) { throw 'No desktop build found. Run scripts/build.ps1 first.' }
if (-not (Test-Path -LiteralPath $executable)) {
    throw 'The built app is missing. Build the frontend and the custom-protocol Rust executable first, or use scripts/dev.ps1.'
}
if (-not $env:ORCHFLOW_PYTHON) {
    $imagingPython = Join-Path $projectRoot '.venv-imaging\Scripts\python.exe'
    $runtimeRoot = if ($env:ORCHFLOW_USD_ROOT) { $env:ORCHFLOW_USD_ROOT } else { 'I:\AI\OpenUSD-runtime\25.08' }
    if ((Test-Path -LiteralPath $imagingPython) -and (Test-Path -LiteralPath (Join-Path $runtimeRoot 'lib\python\pxr\UsdImagingGL'))) {
        $env:ORCHFLOW_PYTHON = $imagingPython
        $env:ORCHFLOW_USD_ROOT = $runtimeRoot
    } else {
        $env:ORCHFLOW_PYTHON = Join-Path $projectRoot '.venv\Scripts\python.exe'
    }
}
if (-not $env:ORCHFLOW_OVRTX_PYTHON) {
    $ovrtxPython = Join-Path $projectRoot '.venv-ovrtx\Scripts\python.exe'
    if (Test-Path -LiteralPath $ovrtxPython) {
        $env:ORCHFLOW_OVRTX_PYTHON = $ovrtxPython
    }
}
if (-not (Test-Path -LiteralPath $env:ORCHFLOW_PYTHON)) { throw 'Set up the Python runtime using README.md first.' }
Push-Location $projectRoot
try { & $executable } finally { Pop-Location }
