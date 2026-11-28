param(
    [string]$InstallRoot,
    [switch]$Silent
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

function Test-DirWritable([string]$dir) {
    try {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $probe = Join-Path $dir ('.write-test-' + [guid]::NewGuid().ToString('N'))
        [System.IO.File]::WriteAllText($probe, '')
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        return $true
    } catch {
        return $false
    }
}

$programRuntimes = Join-Path (Split-Path -Parent $PSScriptRoot) 'runtimes'
$userRuntimes = Join-Path $env:LOCALAPPDATA 'Sublayer\runtimes'
if ($InstallRoot -and (Test-DirWritable $InstallRoot)) {
    $script:RuntimesRoot = $InstallRoot
} elseif (Test-DirWritable $programRuntimes) {
    $script:RuntimesRoot = $programRuntimes
} else {
    $script:RuntimesRoot = $userRuntimes
    New-Item -ItemType Directory -Force -Path $script:RuntimesRoot | Out-Null
}

$script:OpenUsdUrl = 'https://developer.nvidia.com/downloads/usd/usd_binaries/25.08/usd.py312.windows-x86_64.usdview.release-v25.08.71e038c1.zip'
$script:OpenUsdSha256 = '61BAE28D18C873871047E7A8B3FE1FFE2188FB88FDDE113BE429812D27F0C8B4'

function Find-Uv {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($cand in @(
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
        (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\uv\uv.exe')
    )) {
        if (Test-Path -LiteralPath $cand) { return $cand }
    }
    return $null
}

function Test-OpenUsdInstalled {
    $usd = Join-Path $script:RuntimesRoot 'openusd-25.08\lib\python\pxr\UsdImagingGL'
    $py = Join-Path $script:RuntimesRoot 'venv-imaging\Scripts\python.exe'
    return ((Test-Path -LiteralPath $usd) -or ($env:ORCHFLOW_USD_ROOT -and (Test-Path -LiteralPath (Join-Path $env:ORCHFLOW_USD_ROOT 'lib\python\pxr\UsdImagingGL')))) -and (Test-Path -LiteralPath $py)
}

function Get-OvrtxRequirements {
    $bundled = Join-Path $PSScriptRoot 'requirements-ovrtx.txt'
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    return (Join-Path $PSScriptRoot '../worker/requirements-ovrtx.txt')
}

function Test-OvrtxInstalled {
    $manifest = Join-Path $script:RuntimesRoot 'ovrtx\runtime.json'
    $py = Join-Path $script:RuntimesRoot 'venv-ovrtx\Scripts\python.exe'
    $pxr = Join-Path $script:RuntimesRoot 'venv-ovrtx\Lib\site-packages\pxr\Usd\__init__.py'
    if (-not ((Test-Path -LiteralPath $manifest) -and (Test-Path -LiteralPath $py) -and (Test-Path -LiteralPath $pxr))) { return $false }
    try {
        $status = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
        return $status.requirementsHash -eq (Get-FileHash -LiteralPath (Get-OvrtxRequirements)).Hash
    } catch { return $false }
}

function Test-AuroraInstalled {
    $exeDir = Split-Path -Parent $PSScriptRoot
    foreach ($cand in @(
        $env:ORCHFLOW_AURORA_ROOT,
        (Join-Path $exeDir 'renderers\aurora'),
        (Join-Path $exeDir 'resources\renderers\aurora'),
        (Join-Path $script:RuntimesRoot 'aurora'),
        'I:\AI\OrchFlow-renderers\aurora'
    )) {
        if ($cand -and ((Test-Path -LiteralPath (Join-Path $cand 'runtime.json')) -or (Test-Path -LiteralPath (Join-Path $cand 'bin\usd\hdAurora\resources\plugInfo.json')))) {
            return $cand
        }
    }
    return $null
}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Sublayer — Optional Runtime Installer'
$form.Size = New-Object System.Drawing.Size(720, 600)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.BackColor = [System.Drawing.Color]::FromArgb(24, 28, 34)
$form.ForeColor = [System.Drawing.Color]::FromArgb(235, 240, 246)
$form.Font = New-Object System.Drawing.Font('Segoe UI', 9.5)

$title = New-Object System.Windows.Forms.Label
$title.Text = 'Choose runtimes to install from their official sources'
$title.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 12)
$title.Location = New-Object System.Drawing.Point(18, 14)
$title.Size = New-Object System.Drawing.Size(670, 26)
$form.Controls.Add($title)

$subtitle = New-Object System.Windows.Forms.Label
$subtitle.Text = "Runtimes are installed into $script:RuntimesRoot. Proprietary NVIDIA packages are downloaded directly by you from official sources."
$subtitle.ForeColor = [System.Drawing.Color]::FromArgb(170, 180, 194)
$subtitle.Location = New-Object System.Drawing.Point(18, 42)
$subtitle.Size = New-Object System.Drawing.Size(670, 34)
$form.Controls.Add($subtitle)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Multiline = $true
$logBox.ReadOnly = $true
$logBox.ScrollBars = 'Vertical'
$logBox.BackColor = [System.Drawing.Color]::FromArgb(14, 17, 21)
$logBox.ForeColor = [System.Drawing.Color]::FromArgb(205, 214, 224)
$logBox.Font = New-Object System.Drawing.Font('Consolas', 8.5)
$logBox.Location = New-Object System.Drawing.Point(18, 336)
$logBox.Size = New-Object System.Drawing.Size(668, 132)
$form.Controls.Add($logBox)

$activityLabel = New-Object System.Windows.Forms.Label
$activityLabel.Location = New-Object System.Drawing.Point(18, 474)
$activityLabel.Size = New-Object System.Drawing.Size(668, 22)
$activityLabel.Text = 'Ready'
$form.Controls.Add($activityLabel)

$progressBar = New-Object System.Windows.Forms.ProgressBar
$progressBar.Location = New-Object System.Drawing.Point(18, 498)
$progressBar.Size = New-Object System.Drawing.Size(668, 12)
$form.Controls.Add($progressBar)
$script:Busy = $false
$form.Add_FormClosing({ param($sender, $eventArgs) $eventArgs.Cancel = $script:Busy })

function Write-Log([string]$msg) {
    if ($logBox.IsDisposed) { return }
    $logBox.AppendText("$msg`r`n")
    $logBox.SelectionStart = $logBox.Text.Length
    $logBox.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
}

function Add-RuntimeRow([int]$y, [string]$name, [string]$desc, [bool]$defaultChecked, [scriptblock]$onInstall) {
    $panel = New-Object System.Windows.Forms.Panel
    $panel.Location = New-Object System.Drawing.Point(18, $y)
    $panel.Size = New-Object System.Drawing.Size(668, 58)
    $panel.BackColor = [System.Drawing.Color]::FromArgb(32, 37, 45)

    $chk = New-Object System.Windows.Forms.CheckBox
    $chk.Text = $name
    $chk.Checked = $defaultChecked
    $chk.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 9.5)
    $chk.Location = New-Object System.Drawing.Point(10, 6)
    $chk.Size = New-Object System.Drawing.Size(420, 22)
    $panel.Controls.Add($chk)

    $statusLbl = New-Object System.Windows.Forms.Label
    $statusLbl.Text = 'Checking...'
    $statusLbl.TextAlign = 'MiddleRight'
    $statusLbl.Location = New-Object System.Drawing.Point(435, 6)
    $statusLbl.Size = New-Object System.Drawing.Size(115, 22)
    $panel.Controls.Add($statusLbl)

    $descLbl = New-Object System.Windows.Forms.Label
    $descLbl.Text = $desc
    $descLbl.ForeColor = [System.Drawing.Color]::FromArgb(170, 180, 194)
    $descLbl.Location = New-Object System.Drawing.Point(28, 30)
    $descLbl.Size = New-Object System.Drawing.Size(525, 22)
    $panel.Controls.Add($descLbl)

    $btn = New-Object System.Windows.Forms.Button
    $btn.Text = 'Install'
    $btn.FlatStyle = 'Flat'
    $btn.BackColor = [System.Drawing.Color]::FromArgb(49, 108, 244)
    $btn.ForeColor = [System.Drawing.Color]::White
    $btn.Location = New-Object System.Drawing.Point(562, 13)
    $btn.Size = New-Object System.Drawing.Size(94, 30)
    $btn.Add_Click($onInstall)
    $panel.Controls.Add($btn)

    $form.Controls.Add($panel)
    return @{ Check = $chk; Status = $statusLbl; Button = $btn }
}

function Invoke-StreamingCommand([string]$command) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'powershell.exe'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes("`$ProgressPreference='SilentlyContinue';`n$command"))
    $psi.Arguments = "-NoProfile -NonInteractive -OutputFormat Text -ExecutionPolicy Bypass -EncodedCommand $encoded"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $elapsed = [Diagnostics.Stopwatch]::StartNew()
    $lastOutput = 0
    $progressBar.Style = 'Marquee'
    try {
        $proc.Start() | Out-Null
        # Drain both pipes concurrently. EndOfStream/ReadLine block the UI thread
        # during quiet steps, and an unread stderr pipe can deadlock installers.
        $stdout = $proc.StandardOutput.ReadLineAsync()
        $stderr = $proc.StandardError.ReadLineAsync()
        while ($null -ne $stdout -or $null -ne $stderr -or -not $proc.HasExited) {
            foreach ($stream in @('stdout', 'stderr')) {
                $pending = Get-Variable -Name $stream -ValueOnly
                $drained = 0
                while ($null -ne $pending -and $pending.IsCompleted -and $drained -lt 100) {
                    $drained++
                    $line = $pending.GetAwaiter().GetResult()
                    if ($null -eq $line) {
                        Set-Variable -Name $stream -Value $null
                    } else {
                        $lastOutput = $elapsed.Elapsed.TotalSeconds
                        if ($stream -eq 'stdout' -and $line -match '^PROGRESS\|(\d+)\|(.*)$') {
                            $progressBar.Style = 'Continuous'
                            $progressBar.Value = [Math]::Min(100, [int]$Matches[1])
                            Write-Log $Matches[2]
                        } elseif ($line) {
                            $progressBar.Style = 'Marquee'
                            Write-Log $line
                        }
                        $reader = if ($stream -eq 'stdout') { $proc.StandardOutput } else { $proc.StandardError }
                        Set-Variable -Name $stream -Value ($reader.ReadLineAsync())
                    }
                    $pending = Get-Variable -Name $stream -ValueOnly
                }
            }
            $quiet = [int]($elapsed.Elapsed.TotalSeconds - $lastOutput)
            $activityLabel.Text = "Running - elapsed $($elapsed.Elapsed.ToString('hh\:mm\:ss')); last output $quiet s ago"
            [System.Windows.Forms.Application]::DoEvents()
            Start-Sleep -Milliseconds 60
        }
        $proc.WaitForExit()
        if ($proc.ExitCode -ne 0) {
            $activityLabel.Text = "Failed after $($elapsed.Elapsed.ToString('hh\:mm\:ss')) - see log"
            throw "Command failed with exit code $($proc.ExitCode)"
        }
        $activityLabel.Text = "Completed in $($elapsed.Elapsed.ToString('hh\:mm\:ss'))"
    } finally {
        $progressBar.Style = 'Continuous'
        $proc.Dispose()
    }
}

# Sent to the worker process so decompression never runs on the window thread.
$script:ExtractWithProgress = {
    param([string]$Archive, [string]$Destination)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $root = [IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
    [IO.Directory]::CreateDirectory($root) | Out-Null
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        $total = $zip.Entries.Count
        $done = 0
        [long]$bytes = 0
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $nextReport = 0
        foreach ($entry in $zip.Entries) {
            $target = [IO.Path]::GetFullPath([IO.Path]::Combine($root, $entry.FullName))
            if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Archive entry escapes destination: $($entry.FullName)"
            }
            if (-not $entry.Name) {
                [IO.Directory]::CreateDirectory($target) | Out-Null
            } else {
                [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
                $bytes += $entry.Length
            }
            $done++
            if ($clock.Elapsed.TotalSeconds -ge $nextReport -or $done -eq $total) {
                $percent = [int][Math]::Floor(100.0 * $done / [Math]::Max(1, $total))
                Write-Output "PROGRESS|$percent|Extracting OpenUSD: $done / $total entries ($percent%); $([Math]::Round($bytes / 1MB)) MB extracted"
                $nextReport = $clock.Elapsed.TotalSeconds + 1
            }
        }
    } finally { $zip.Dispose() }
}

function Install-Uv {
    Write-Log '--- Installing uv from https://astral.sh/uv/install.ps1 ---'
    Invoke-StreamingCommand "`$ProgressPreference='SilentlyContinue'; irm https://astral.sh/uv/install.ps1 | iex"
    Write-Log 'uv installed.'
}

function Install-OpenUsd {
    $uv = Find-Uv
    if (-not $uv) {
        Install-Uv
        $uv = Find-Uv
        if (-not $uv) { throw 'uv not found after installation.' }
    }
    $usdDest = Join-Path $script:RuntimesRoot 'openusd-25.08'
    $zipPath = Join-Path $script:RuntimesRoot 'usd-25.08-win64.zip'
    $venvDir = Join-Path $script:RuntimesRoot 'venv-imaging'
    $venvPy = Join-Path $venvDir 'Scripts\python.exe'
    Write-Log '--- Installing OpenUSD 25.08 + Imaging Python ---'
    $cmd = @"
`$ErrorActionPreference='Stop'
`$ProgressPreference='SilentlyContinue'
if (-not (Test-Path -LiteralPath '$usdDest\lib\python\pxr\UsdImagingGL')) {
  Write-Output 'Downloading NVIDIA OpenUSD 25.08 (~486 MB)...'
  Invoke-WebRequest -Uri '$($script:OpenUsdUrl)' -OutFile '$zipPath'
  Write-Output 'Verifying SHA-256 checksum...'
  `$hash = (Get-FileHash -LiteralPath '$zipPath' -Algorithm SHA256).Hash
  if (`$hash -ne '$($script:OpenUsdSha256)') {
    Remove-Item -LiteralPath '$zipPath' -Force -ErrorAction SilentlyContinue
    throw "SHA-256 mismatch (`$hash)"
  }
  Write-Output 'Extracting OpenUSD 25.08...'
  New-Item -ItemType Directory -Force -Path '$usdDest' | Out-Null
  & { $($script:ExtractWithProgress.ToString()) } -Archive '$zipPath' -Destination '$usdDest'
  Remove-Item -LiteralPath '$zipPath' -Force -ErrorAction SilentlyContinue
} else {
  Write-Output 'OpenUSD 25.08 already present.'
}
if (-not (Test-Path -LiteralPath '$venvPy')) {
  Write-Output 'Creating Python 3.12 imaging environment...'
  & '$uv' venv --python 3.12 '$venvDir'
  if (`$LASTEXITCODE -ne 0) { throw 'Failed to create Python 3.12 venv' }
}
Write-Output 'Installing PySide6 and PyOpenGL...'
& '$uv' pip install --python '$venvPy' 'PySide6>=6.7,<6.9' 'PyOpenGL>=3.1'
if (`$LASTEXITCODE -ne 0) { throw 'Failed to install imaging packages' }
Write-Output 'OpenUSD 25.08 + Imaging Python ready.'
"@
    Invoke-StreamingCommand $cmd
}

function Install-Ovrtx {
    $uv = Find-Uv
    if (-not $uv) {
        Install-Uv
        $uv = Find-Uv
        if (-not $uv) { throw 'uv not found after installation.' }
    }
    $ovrtxRoot = Join-Path $script:RuntimesRoot 'ovrtx'
    $venvDir = Join-Path $script:RuntimesRoot 'venv-ovrtx'
    $venvPy = Join-Path $venvDir 'Scripts\python.exe'
    $manifest = Join-Path $ovrtxRoot 'runtime.json'
    $requirements = Get-OvrtxRequirements
    $requirementsHash = (Get-FileHash -LiteralPath $requirements).Hash
    Write-Log '--- Installing NVIDIA Omniverse RTX (ovrtx) ---'
    $cmd = @"
`$ErrorActionPreference='Stop'
if (-not (Test-Path -LiteralPath '$venvPy')) {
  Write-Output 'Creating Python 3.12 environment for Omniverse RTX...'
  & '$uv' venv --python 3.12 '$venvDir'
  if (`$LASTEXITCODE -ne 0) { throw 'Failed to create Python 3.12 venv' }
}
Write-Output 'Installing ovrtx, ovstage, numpy, pillow...'
& '$uv' pip install --python '$venvPy' -r '$requirements'
if (`$LASTEXITCODE -ne 0) { throw 'Failed to install ovrtx packages' }
New-Item -ItemType Directory -Force -Path '$ovrtxRoot' | Out-Null
`$json = & '$venvPy' -B -c "import json, sys, ovrtx, ovstage; from pxr import Usd; assert Usd.Stage.CreateInMemory(); ovrtx.register_schema_paths(); print(json.dumps({'name':'NVIDIA Omniverse RTX','installed':True,'available':True,'ovrtxVersion':str(getattr(ovrtx,'__version__','unknown')),'ovstageVersion':str(getattr(ovstage,'__version__','unknown')),'pythonExecutable':sys.executable,'features':['Real-Time Ray Tracing (RTX)','Path Tracing (RTX)','MaterialX Shading','MDL Shading'],'message':'Omniverse RTX runtime ready.'}, indent=2))"
if (`$LASTEXITCODE -ne 0) { throw 'Omniverse RTX probe failed' }
`$status = (`$json -join [Environment]::NewLine) | ConvertFrom-Json
`$status | Add-Member -NotePropertyName requirementsHash -NotePropertyValue '$requirementsHash'
[System.IO.File]::WriteAllText('$manifest', (`$status | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new(`$false))
Write-Output 'NVIDIA Omniverse RTX ready.'
"@
    Invoke-StreamingCommand $cmd
}

$rowUv = Add-RuntimeRow 84 'uv (Python & Package Manager)' 'Official Astral installer (https://astral.sh/uv/install.ps1)' (-not [bool](Find-Uv)) {
    try { Set-Busy $true; Install-Uv } catch { Write-Log "ERROR: $_" } finally { Set-Busy $false; Update-Statuses }
}
$rowUsd = Add-RuntimeRow 148 'OpenUSD 25.08 + Hydra Storm (Required for 3D)' 'Official NVIDIA OpenUSD 25.08 zip (SHA-256 verified) + PySide6 & PyOpenGL' (-not (Test-OpenUsdInstalled)) {
    try { Set-Busy $true; Install-OpenUsd } catch { Write-Log "ERROR: $_" } finally { Set-Busy $false; Update-Statuses }
}
$rowOvrtx = Add-RuntimeRow 212 'NVIDIA Omniverse RTX (Optional, RTX GPU)' 'Installs official ovrtx & ovstage packages into an isolated Python 3.12 venv' $false {
    try { Set-Busy $true; Install-Ovrtx } catch { Write-Log "ERROR: $_" } finally { Set-Busy $false; Update-Statuses }
}
$rowAurora = Add-RuntimeRow 276 'Autodesk Aurora (Hydra Delegate)' 'Detected from bundled resources or existing installation' $false {
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = 'Select an Autodesk Aurora runtime folder (containing runtime.json)'
    if ($dlg.ShowDialog() -eq 'OK') {
        $target = Join-Path $script:RuntimesRoot 'aurora'
        if ((Test-Path -LiteralPath (Join-Path $dlg.SelectedPath 'runtime.json')) -or (Test-Path -LiteralPath (Join-Path $dlg.SelectedPath 'bin\usd\hdAurora\resources\plugInfo.json'))) {
            Write-Log "Copying Aurora runtime from $($dlg.SelectedPath) to $target..."
            Copy-Item -LiteralPath $dlg.SelectedPath -Destination $target -Recurse -Force
            Write-Log 'Aurora runtime linked.'
        } else {
            Write-Log 'Selected folder does not contain runtime.json or hdAurora plugInfo.json.'
        }
        Update-Statuses
    }
}
$rowAurora.Check.Enabled = $false
$rowAurora.Button.Text = 'Locate...'

$installSelBtn = New-Object System.Windows.Forms.Button
$installSelBtn.Text = 'Install Selected'
$installSelBtn.FlatStyle = 'Flat'
$installSelBtn.BackColor = [System.Drawing.Color]::FromArgb(37, 135, 88)
$installSelBtn.ForeColor = [System.Drawing.Color]::White
$installSelBtn.Location = New-Object System.Drawing.Point(18, 520)
$installSelBtn.Size = New-Object System.Drawing.Size(150, 34)
$form.Controls.Add($installSelBtn)

$closeBtn = New-Object System.Windows.Forms.Button
$closeBtn.Text = 'Continue / Close'
$closeBtn.FlatStyle = 'Flat'
$closeBtn.BackColor = [System.Drawing.Color]::FromArgb(56, 62, 74)
$closeBtn.ForeColor = [System.Drawing.Color]::White
$closeBtn.Location = New-Object System.Drawing.Point(536, 520)
$closeBtn.Size = New-Object System.Drawing.Size(150, 34)
$closeBtn.Add_Click({ $form.Close() })
$form.Controls.Add($closeBtn)

function Set-Busy([bool]$busy) {
    $script:Busy = $busy
    foreach ($r in @($rowUv, $rowUsd, $rowOvrtx, $rowAurora)) {
        $r.Button.Enabled = -not $busy
        $r.Check.Enabled = (-not $busy) -and ($r -ne $rowAurora)
    }
    $installSelBtn.Enabled = -not $busy
    $closeBtn.Enabled = -not $busy
    [System.Windows.Forms.Application]::DoEvents()
}

function Update-Statuses {
    $uvOk = [bool](Find-Uv)
    $rowUv.Status.Text = if ($uvOk) { 'Installed' } else { 'Not installed' }
    $rowUv.Status.ForeColor = if ($uvOk) { [System.Drawing.Color]::LightGreen } else { [System.Drawing.Color]::Khaki }

    $usdOk = Test-OpenUsdInstalled
    $rowUsd.Status.Text = if ($usdOk) { 'Installed' } else { 'Not installed' }
    $rowUsd.Status.ForeColor = if ($usdOk) { [System.Drawing.Color]::LightGreen } else { [System.Drawing.Color]::Salmon }

    $rtxOk = Test-OvrtxInstalled
    $rowOvrtx.Status.Text = if ($rtxOk) { 'Installed' } else { 'Optional' }
    $rowOvrtx.Status.ForeColor = if ($rtxOk) { [System.Drawing.Color]::LightGreen } else { [System.Drawing.Color]::Silver }

    $aurPath = Test-AuroraInstalled
    $rowAurora.Status.Text = if ($aurPath) { 'Installed' } else { 'Not found' }
    $rowAurora.Status.ForeColor = if ($aurPath) { [System.Drawing.Color]::LightGreen } else { [System.Drawing.Color]::Silver }
}

$installSelBtn.Add_Click({
    try {
        Set-Busy $true
        if ($rowUv.Check.Checked -and -not (Find-Uv)) { Install-Uv }
        if ($rowUsd.Check.Checked) { Install-OpenUsd }
        if ($rowOvrtx.Check.Checked) { Install-Ovrtx }
        Write-Log 'Selected installations finished.'
    } catch {
        Write-Log "ERROR: $_"
    } finally {
        Set-Busy $false
        Update-Statuses
    }
})

Update-Statuses
if ($Silent) {
    # Repair an existing RTX install created by an older wizard. A silent
    # install on a clean machine does not opt into downloading RTX.
    if ((Test-Path -LiteralPath (Join-Path $script:RuntimesRoot 'ovrtx\runtime.json')) -and -not (Test-OvrtxInstalled)) {
        Install-Ovrtx
    }
} else {
    [void]$form.ShowDialog()
}
