param(
    [string]$Targets = 'nsis',
    [switch]$IncludeAurora = $true,
    [string]$AuroraSource = 'I:/AI/OrchFlow-renderers/aurora'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$tauriConfPath = Join-Path $projectRoot 'src-tauri\tauri.conf.json'
if (-not $env:CARGO_TARGET_DIR) {
    $env:CARGO_TARGET_DIR = 'I:\AI\OrchFlow-build'
}
$stagedAurora = Join-Path $env:CARGO_TARGET_DIR 'staged-aurora'
$stagedJunction = Join-Path $projectRoot 'src-tauri\staged-aurora'

$originalConf = Get-Content -LiteralPath $tauriConfPath -Raw -Encoding UTF8

Push-Location $projectRoot
try {
    if ($IncludeAurora) {
        if (-not (Test-Path -LiteralPath (Join-Path $AuroraSource 'runtime.json'))) {
            throw "Aurora runtime.json not found at $AuroraSource"
        }
        $manifest = Get-Content -LiteralPath (Join-Path $AuroraSource 'runtime.json') -Raw | ConvertFrom-Json
        $binRel = if ($manifest.binaryRoot) { $manifest.binaryRoot } else { 'bin' }
        $binSrc = Join-Path $AuroraSource $binRel
        if (-not (Test-Path -LiteralPath $binSrc)) {
            throw "Aurora binary root not found at $binSrc"
        }
        if (Test-Path -LiteralPath $stagedAurora) {
            Remove-Item -LiteralPath $stagedAurora -Recurse -Force
        }
        New-Item -ItemType Directory -Force -Path (Join-Path $stagedAurora 'bin') | Out-Null
        Copy-Item -Path (Join-Path $binSrc '*') -Destination (Join-Path $stagedAurora 'bin') -Recurse -Force
        # Remove optional proprietary NVIDIA MDL SDK DLLs unless explicitly desired
        Get-ChildItem -Path (Join-Path $stagedAurora 'bin') -Filter 'libmdl_*.dll' -ErrorAction SilentlyContinue | Remove-Item -Force
        if (Test-Path -LiteralPath (Join-Path $AuroraSource 'licenses')) {
            Copy-Item -Path (Join-Path $AuroraSource 'licenses\*') -Destination (New-Item -ItemType Directory -Force -Path (Join-Path $stagedAurora 'licenses')).FullName -Recurse -Force
        }
        $revLicenses = Join-Path (Split-Path -Parent $binSrc) 'licenses'
        if (Test-Path -LiteralPath $revLicenses) {
            Copy-Item -Path (Join-Path $revLicenses '*') -Destination (New-Item -ItemType Directory -Force -Path (Join-Path $stagedAurora 'licenses')).FullName -Recurse -Force
        }
        $stagedManifest = [ordered]@{
            auroraVersion = $manifest.auroraVersion
            usdVersion = $manifest.usdVersion
            materialXVersion = $manifest.materialXVersion
            binaryRoot = 'bin'
            pluginInfo = 'bin/usd/hdAurora/resources/plugInfo.json'
        } | ConvertTo-Json -Depth 4
        [System.IO.File]::WriteAllText((Join-Path $stagedAurora 'runtime.json'), $stagedManifest, [System.Text.UTF8Encoding]::new($false))

        if (Test-Path -LiteralPath $stagedJunction) {
            cmd /c rmdir "$stagedJunction" 2>$null
            if (Test-Path -LiteralPath $stagedJunction) { Remove-Item -LiteralPath $stagedJunction -Recurse -Force }
        }
        New-Item -ItemType Junction -Path $stagedJunction -Target $stagedAurora | Out-Null
        $confJson = $originalConf | ConvertFrom-Json
        $confJson.bundle.resources | Add-Member -NotePropertyName 'staged-aurora/' -NotePropertyValue 'renderers/aurora/' -Force
        [System.IO.File]::WriteAllText($tauriConfPath, ($confJson | ConvertTo-Json -Depth 10), [System.Text.UTF8Encoding]::new($false))
        Write-Host "Staged Autodesk Aurora (open-source build) into installer resources."
    }

    Write-Host "Building Sublayer Windows installer ($Targets)..."
    & npm.cmd run tauri -- build --bundles $Targets
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri bundle build failed with exit code $LASTEXITCODE"
    }

    $bundleDir = Join-Path $env:CARGO_TARGET_DIR 'release\bundle'
    if (Test-Path -LiteralPath $bundleDir) {
        Write-Host "`nGenerated installer(s):" -ForegroundColor Green
        Get-ChildItem -Path $bundleDir -Recurse -File -Include *.exe,*.msi | ForEach-Object {
            Write-Host "  $($_.FullName) ($([math]::Round($_.Length / 1MB, 2)) MB)"
        }
    }
} finally {
    [System.IO.File]::WriteAllText($tauriConfPath, $originalConf, [System.Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $stagedJunction) {
        cmd /c rmdir "$stagedJunction" 2>$null
    }
    if (Test-Path -LiteralPath $stagedAurora) {
        Remove-Item -LiteralPath $stagedAurora -Recurse -Force -ErrorAction SilentlyContinue
    }
    Pop-Location
}
