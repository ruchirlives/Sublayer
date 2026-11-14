param(
    [string]$InstallRoot = 'I:/AI/OrchFlow-renderers/ovrtx',
    [string]$PythonVersion = '3.12'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

function Invoke-Checked([scriptblock]$Action) {
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
}

Write-Host "=== Setting up NVIDIA Omniverse RTX (ovrtx) standalone runtime ==="

# 1. Verify uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv package manager is required. Install uv or ensure it is in PATH.'
}

# 2. Check NVIDIA driver and GPU
$gpuName = "NVIDIA RTX GPU"
$driverVersion = "Unknown"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    try {
        $smiOut = nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
        if ($smiOut) {
            $parts = $smiOut -split ','
            $gpuName = $parts[0].Trim()
            $driverVersion = $parts[1].Trim()
            Write-Host "Detected GPU: $gpuName (Driver: $driverVersion)"
        }
    } catch {
        Write-Warning "Could not query nvidia-smi: $_"
    }
}

# 3. Create or update .venv-ovrtx
$venvPath = Join-Path $projectRoot '.venv-ovrtx'
$venvPython = Join-Path $venvPath 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating isolated Python $PythonVersion environment at $venvPath..."
    Invoke-Checked { uv venv --python $PythonVersion $venvPath }
} else {
    Write-Host "Reusing existing virtual environment at $venvPath."
}

# 4. Install requirements
$reqs = Join-Path $projectRoot 'worker/requirements-ovrtx.txt'
Write-Host "Installing ovrtx, ovstage, numpy, and pillow into .venv-ovrtx..."
Invoke-Checked { uv pip install --python $venvPython -r $reqs }

# 5. Probe runtime capability and save runtime.json
Write-Host "Probing ovrtx runtime capability..."
$probeScript = @'
import json, sys, os
try:
    import ovrtx
    import ovstage
    import numpy
    from PIL import Image

    ovrtx_ver = getattr(ovrtx, '__version__', 'unknown')
    ovstage_ver = getattr(ovstage, '__version__', 'unknown')

    # Register schemas to verify schema loading
    ovrtx.register_schema_paths()

    status = {
        "name": "NVIDIA Omniverse RTX",
        "installed": True,
        "available": True,
        "ovrtxVersion": str(ovrtx_ver),
        "ovstageVersion": str(ovstage_ver),
        "pythonExecutable": sys.executable,
        "features": [
            "Real-Time Ray Tracing (RTX)",
            "Path Tracing (RTX)",
            "MaterialX Shading",
            "MDL Shading",
            "Sensor Simulation",
            "ovstage Scene Ingestion"
        ],
        "message": "Omniverse RTX runtime ready."
    }
    print("ORCHFLOW_PROBE:" + json.dumps(status))
except Exception as e:
    err_status = {
        "name": "NVIDIA Omniverse RTX",
        "installed": False,
        "available": False,
        "message": str(e)
    }
    print("ORCHFLOW_PROBE:" + json.dumps(err_status))
    sys.exit(1)
'@

$probeOut = $probeScript | & $venvPython -
$probeJson = $null
foreach ($line in ($probeOut -split "`r?`n")) {
    if ($line.StartsWith("ORCHFLOW_PROBE:")) {
        $probeJson = $line.Substring("ORCHFLOW_PROBE:".Length)
        break
    }
}

if (-not $probeJson) {
    throw "Runtime probe failed: $probeOut"
}

$status = ConvertFrom-Json $probeJson
$status | Add-Member -NotePropertyName "device" -NotePropertyValue $gpuName -Force
$status | Add-Member -NotePropertyName "driverVersion" -NotePropertyValue $driverVersion -Force
$status | Add-Member -NotePropertyName "installRoot" -NotePropertyValue $InstallRoot -Force

# 6. Write runtime.json manifest
if (-not (Test-Path -LiteralPath $InstallRoot)) {
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
}
$manifestPath = Join-Path $InstallRoot 'runtime.json'
$manifestContent = $status | ConvertTo-Json -Depth 5
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($manifestPath, $manifestContent, $utf8NoBom)

Write-Host "Runtime manifest written to $manifestPath."
Write-Host "NVIDIA Omniverse RTX setup completed successfully."
