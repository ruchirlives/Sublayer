param(
    [string]$PythonExe = "$PSScriptRoot/../.venv-imaging/Scripts/python.exe",
    [string]$SourceDir = 'I:/AI/OpenRigLogic',
    [string]$BuildRoot = 'I:/AI/OpenRigLogic-build',
    [string]$RuntimeRoot = 'I:/AI/OpenRigLogic-runtime',
    [string]$SwigExe = 'I:/AI/MetaHuman-tools/swig/data/bin/swig.exe',
    [string]$SwigLib = 'I:/AI/MetaHuman-tools/swig/data/share/swig/4.5.1'
)
$ErrorActionPreference = 'Stop'
# Epic's immutable 5.8 release; reads both legacy and UE 5.6+ DNA.
$revision = 'd3d329bbd6992ac38adc1c1b24203ceb998f75c0'
if (!(Test-Path -LiteralPath $PythonExe)) { throw "Python not found: $PythonExe" }
if (!(Test-Path -LiteralPath $SwigExe)) { throw 'Install SWIG 4+ separately, then pass -SwigExe and -SwigLib.' }
$pythonVersion = & $PythonExe -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
if ($LASTEXITCODE) { throw 'Cannot determine Python version' }
if (!(Test-Path -LiteralPath $SourceDir)) {
    git clone --depth 1 --branch 5.8 https://github.com/EpicGames/OpenRigLogic.git $SourceDir
    if ($LASTEXITCODE) { throw 'OpenRigLogic clone failed' }
}
$actual = git -C $SourceDir rev-parse HEAD
if ($LASTEXITCODE -or $actual -ne $revision) {
    throw "Expected Epic OpenRigLogic revision $revision. Use a separate checkout at that revision."
}
$buildDir = Join-Path $BuildRoot "py$pythonVersion"
$runtimeDir = Join-Path $RuntimeRoot "py$pythonVersion"
cmake -S $SourceDir -B $buildDir -G 'Visual Studio 17 2022' -A x64 `
    "-DRL_BUILD_PYTHON_WRAPPER=$pythonVersion" '-DRL_BUILD_TESTS=OFF' `
    '-DRL_BUILD_EXAMPLES=OFF' '-DRL_BUILD_BENCHMARKS=OFF' '-DBUILD_SHARED_LIBS=ON' `
    "-DSWIG_EXECUTABLE=$SwigExe" "-DSWIG_DIR=$SwigLib" `
    "-DPython3_EXECUTABLE=$((Resolve-Path -LiteralPath $PythonExe).Path)" `
    "-DCMAKE_INSTALL_PREFIX=$runtimeDir"
if ($LASTEXITCODE) { throw 'OpenRigLogic configure failed' }
cmake --build $buildDir --config Release --target py3dna -j 8
if ($LASTEXITCODE) { throw 'OpenRigLogic DNA reader build failed' }
cmake --install $buildDir --config Release --component dna
if ($LASTEXITCODE) { throw 'OpenRigLogic DNA runtime install failed' }
# Shared DNA symbols are owned by riglogic4; the DNA component does not install it.
$libDir = Join-Path $runtimeDir 'lib'
Get-ChildItem -LiteralPath (Join-Path $buildDir 'Release') -Filter '*.dll' | Copy-Item -Destination $libDir
Copy-Item -LiteralPath (Join-Path $SourceDir 'LICENSE') -Destination (Join-Path $runtimeDir 'LICENSE-OpenRigLogic.txt')
$env:ORCHFLOW_DNA_ROOT = $RuntimeRoot
Write-Host "DNA reader installed at $libDir. ORCHFLOW_DNA_ROOT=$RuntimeRoot"
Write-Host 'Default development runtime discovery finds this location automatically. Restart Sublayer to use it.'
