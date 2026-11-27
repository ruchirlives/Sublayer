# Deploy script for Sublayer
# This script builds the project, creates a Windows NSIS installer, commits any changes, pushes to GitHub, and uploads the installer to the v0.1.0 release.

$ErrorActionPreference = 'Stop'

# Ensure we are in repository root
Set-Location "e:\CODINGPROJECTS\Rust\Sublayer"

# Function to check for uncommitted changes
function Has-UncommittedChanges {
    $status = git status --porcelain
    return -not [string]::IsNullOrWhiteSpace($status)
}

# Commit any pending changes
if (Has-UncommittedChanges) {
    Write-Host "Staging all changes..."
    git add -A
    $message = "Deploy: latest build and installer $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    git commit -m $message
    Write-Host "Committed changes."
} else {
    Write-Host "No changes to commit."
}

# Push to GitHub
Write-Host "Pushing to origin/main..."
git push origin main

# Build the installer using existing script
Write-Host "Building Windows installer..."
$installerScript = "scripts\bundle-windows.ps1"
if (Test-Path $installerScript) {
    & $installerScript
} else {
    throw "Installer script not found: $installerScript"
}

# Determine installer path (assuming script places it in release\bundle\nsis)
$installerPath = Join-Path $env:CARGO_TARGET_DIR "release\bundle\nsis\Sublayer_0.1.0_x64-setup.exe"
if (-not (Test-Path $installerPath)) {
    throw "Installer not found at $installerPath"
}

# Upload to GitHub release v0.1.0 using gh CLI
$releaseTag = "v0.1.0"
Write-Host "Uploading installer to GitHub release $releaseTag..."
# --clobber replaces existing asset with same name
gh release upload $releaseTag $installerPath --clobber

# Update release notes (append new feature list)
$features = @(
    "- Added PostFX node with ACES tonemapping",
    "- Integrated Substance Painter round‑trip workflow",
    "- Added MetaHuman / RigLogic support",
    "- Fixed various bugs and improved stability"
)
$notes = $features -join "`n"
Write-Host "Updating release notes..."
# Retrieve existing notes, append, then edit
$existingNotes = gh release view $releaseTag --json body --jq .body
$newBody = $existingNotes + "`n`n" + $notes
# Write to temporary file for editing
$tempFile = [System.IO.Path]::GetTempFileName()
Set-Content -Path $tempFile -Value $newBody -Encoding utf8
gh release edit $releaseTag --notes-file $tempFile
Remove-Item $tempFile

Write-Host "Deployment complete."
