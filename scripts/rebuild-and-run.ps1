$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

Write-Host "Stopping existing OrchFlow processes..."
Get-Process -Name "orchflow-updated", "orchflow-mcp", "tauri-app", "msedgewebview2" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue

Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'usd_worker\.py|ovrtx_worker\.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Start-Sleep -Milliseconds 500

Write-Host "Building OrchFlow..."
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Build failed with exit code $LASTEXITCODE"
}

Write-Host "Launching OrchFlow..."
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "run-built.ps1")
