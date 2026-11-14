param([string]$UsdRoot = 'I:\AI\OpenUSD-runtime\25.08')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$downloadUrl = 'https://developer.nvidia.com/downloads/usd/usd_binaries/25.08/usd.py312.windows-x86_64.usdview.release-v25.08.71e038c1.zip'
$expectedHash = '61BAE28D18C873871047E7A8B3FE1FFE2188FB88FDDE113BE429812D27F0C8B4'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw 'Install uv or follow the manual Python 3.12 setup in README.md.' }
if (-not (Test-Path -LiteralPath (Join-Path $UsdRoot 'lib\python\pxr\UsdImagingGL'))) {
    New-Item -ItemType Directory -Force $UsdRoot | Out-Null
    $archive = Join-Path (Split-Path -Parent $UsdRoot) 'usd-25.08-win64.zip'
    if (-not (Test-Path -LiteralPath $archive)) { Invoke-WebRequest -Uri $downloadUrl -OutFile $archive }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expectedHash) { throw 'OpenUSD archive checksum mismatch. No extraction performed.' }
    Expand-Archive -LiteralPath $archive -DestinationPath $UsdRoot
}
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath '.venv-imaging\Scripts\python.exe')) {
        uv venv --python 3.12 .venv-imaging
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create Python 3.12 imaging environment.' }
    }
    uv pip install --python .venv-imaging/Scripts/python.exe -r worker/requirements-imaging.txt
    if ($LASTEXITCODE -ne 0) { throw 'Unable to install imaging dependencies.' }
    $env:ORCHFLOW_USD_ROOT = (Resolve-Path -LiteralPath $UsdRoot).Path
    $env:ORCHFLOW_PYTHON = (Resolve-Path '.venv-imaging\Scripts\python.exe').Path
    '{"operation":"capabilities"}' | & $env:ORCHFLOW_PYTHON worker/usd_worker.py
    if ($LASTEXITCODE -ne 0) { throw 'USD runtime check failed.' }
} finally { Pop-Location }
