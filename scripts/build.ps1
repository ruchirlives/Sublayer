$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildRoot = 'I:/AI/OrchFlow-build'
Push-Location $projectRoot
try {
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    & cargo rustc --manifest-path src-tauri/Cargo.toml --target-dir $buildRoot --bin tauri-app --features tauri/custom-protocol -- -o "$buildRoot/debug/Sublayer.exe"
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed; close the existing app if its executable is locked' }
    foreach ($oldExe in @('sublayer-updated.exe', 'orchflow-updated.exe', 'orchflow-dragfix.exe', 'orchflow-bin.exe', 'orchflow-binary.exe', 'orchflow-handoff.exe', 'orchflow-next.exe', 'tauri-app.exe')) {
        $oldPath = Join-Path $buildRoot "debug/$oldExe"
        if (Test-Path -LiteralPath $oldPath) {
            Remove-Item -LiteralPath $oldPath -Force -ErrorAction SilentlyContinue
        }
    }
    $staleWorker = Join-Path $buildRoot 'debug/worker'
    if (Test-Path -LiteralPath $staleWorker) {
        Remove-Item -LiteralPath $staleWorker -Recurse -Force -ErrorAction SilentlyContinue
    }
    $mcpRunning = Get-Process -Name 'orchflow-mcp*' -ErrorAction SilentlyContinue
    if ($mcpRunning) {
        Write-Warning 'orchflow-mcp is running (held by IDE/MCP client); skipping MCP binary rebuild. Refresh/disconnect the MCP server if you need to rebuild orchflow-mcp.exe.'
    } else {
        & cargo rustc --manifest-path src-tauri/Cargo.toml --target-dir $buildRoot --bin orchflow-mcp -- -o "$buildRoot/debug/orchflow-mcp-updated.exe"
        if ($LASTEXITCODE -ne 0) { throw 'MCP build failed; stop the existing MCP process if its executable is locked' }
        Copy-Item "$buildRoot/debug/orchflow-mcp-updated.exe" "$buildRoot/debug/orchflow-mcp.exe" -Force
    }
} finally { Pop-Location }
