$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression
$source = Join-Path $PSScriptRoot 'installer-runtimes.ps1'
$parseErrors = $null
$tokens = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
# Load only the worker helpers, without opening the installer or touching runtimes.
$functionAst = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-StreamingCommand' }, $true)
Invoke-Expression $functionAst.Extent.Text
$extractAst = $ast.Find({ param($node) $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$script:ExtractWithProgress' }, $true)
Invoke-Expression $extractAst.Extent.Text
$progressBar = [pscustomobject]@{ Style = ''; Value = 0 }
$activityLabel = [pscustomobject]@{ Text = '' }
$script:lines = [Collections.Generic.List[string]]::new()
function Write-Log([string]$message) { $script:lines.Add($message) }
$script:ticks = 0
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 50
$timer.Add_Tick({ $script:ticks++ })
$timer.Start()
try {
    Invoke-StreamingCommand @'
Start-Sleep -Milliseconds 900
for ($i = 0; $i -lt 600; $i++) { [Console]::Error.WriteLine(('x' * 200)) }
[Console]::Out.WriteLine('quotes: "hello"; apostrophe: ''ok''')
[Console]::Out.WriteLine('PROGRESS|75|Extraction 75%')
'@
    if ($script:ticks -lt 5) { throw 'UI event pump blocked during quiet child operation' }
    if ($script:lines.Count -ne 602) { throw "Lost process output: $($script:lines.Count) lines" }
    if ($progressBar.Value -ne 75) { throw 'Progress record was not handled' }
    if (-not $script:lines.Contains('quotes: "hello"; apostrophe: ''ok''')) { throw 'Command quoting changed' }
    Write-Output 'PASS: quiet child stays responsive, concurrent stderr drains, progress and quoting preserved'
    try {
        Invoke-StreamingCommand '[Console]::Error.WriteLine("actual failure"); exit 7'
        throw 'Expected nonzero exit failure'
    } catch {
        if ($_ -notmatch 'exit code 7') { throw }
    }
    if (-not $script:lines.Contains('actual failure')) { throw 'Failure detail lost' }
    Write-Output 'PASS: nonzero exit reports failure and preserves stderr'
} finally { $timer.Stop(); $timer.Dispose() }

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('sublayer-installer-test-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($testRoot) | Out-Null
try {
    $archive = Join-Path $testRoot 'sample.zip'
    $zip = [IO.Compression.ZipFile]::Open($archive, [IO.Compression.ZipArchiveMode]::Create)
    try {
        $entry = $zip.CreateEntry('lib/python/example.txt')
        $writer = [IO.StreamWriter]::new($entry.Open())
        $writer.Write('sample data')
        $writer.Dispose()
    } finally { $zip.Dispose() }
    $destination = Join-Path $testRoot 'out'
    $reports = @(& $script:ExtractWithProgress -Archive $archive -Destination $destination)
    if ([IO.File]::ReadAllText((Join-Path $destination 'lib/python/example.txt')) -ne 'sample data') { throw 'Extraction damaged file' }
    if ($reports[-1] -notmatch '^PROGRESS\|100\|') { throw 'Final extraction progress missing' }
    $zip = [IO.Compression.ZipFile]::Open($archive, [IO.Compression.ZipArchiveMode]::Update)
    try { $zip.CreateEntry('../escaped.txt') | Out-Null } finally { $zip.Dispose() }
    try {
        & $script:ExtractWithProgress -Archive $archive -Destination $destination | Out-Null
        throw 'Expected traversal rejection'
    } catch { if ($_ -notmatch 'escapes destination') { throw } }
    Write-Output 'PASS: ZIP extraction reports completion and rejects path traversal'
} finally {
    $resolved = [IO.Path]::GetFullPath($testRoot)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test cleanup path' }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
