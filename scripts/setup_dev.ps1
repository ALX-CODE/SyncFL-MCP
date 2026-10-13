# Prepare an editable SyncFL checkout without changing its Git history.
[CmdletBinding()]
param(
    [string]$Destination,
    [ValidateSet('none', 'codex', 'claude-desktop', 'claude-code')][string]$Client = 'none',
    [string]$ClientConfig,
    [ValidateSet('3.12', '3.13', '3.14')][string]$PythonVersion = '3.13',
    [switch]$NoBootstrap,
    [switch]$InstallFlIntegration,
    [switch]$RunTests,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
$Repository = 'https://github.com/ALX-CODE/SyncFL-MCP.git'
if ($ClientConfig -and $Client -eq 'none') { throw '-ClientConfig requires an explicit -Client.' }
foreach ($Name in @('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR')) {
    if ([Environment]::GetEnvironmentVariable($Name)) {
        throw "Unset $Name before running setup so Git cannot target a different checkout."
    }
}
if ($PSBoundParameters.ContainsKey('Destination') -and [string]::IsNullOrWhiteSpace($Destination)) {
    throw '-Destination must name a checkout directory.'
}
$Git = Get-Command git -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $Git) { throw 'Git is required. Install Git for Windows, then rerun this script.' }

function Test-CanonicalOrigin([string]$Origin) {
    # Accept the normal credential-free HTTPS and SSH forms of this repository.
    return $Origin -match '^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)ALX-CODE/SyncFL-MCP(?:\.git)?/?$'
}

function Get-CheckoutState([string]$Directory) {
    if (-not (Test-Path -LiteralPath $Directory)) { return 'clone' }
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        throw "Destination is not a directory: $Directory"
    }
    if (-not (Get-ChildItem -LiteralPath $Directory -Force | Select-Object -First 1)) {
        return 'clone'
    }
    $Top = & $Git.Source -C $Directory rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $Top) {
        throw "Destination is nonempty and is not a Git checkout: $Directory"
    }
    if ([IO.Path]::GetFullPath([string]$Top).TrimEnd('\', '/') -ne $Directory.TrimEnd('\', '/')) {
        throw "Destination must be the exact Git root, not a directory inside another checkout: $Directory"
    }
    $Origin = & $Git.Source -C $Directory remote get-url origin 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $Origin -or -not (Test-CanonicalOrigin ([string]$Origin))) {
        throw "Destination origin must be $Repository. Existing files and Git settings were left unchanged."
    }
    return 'reuse'
}

if (-not $Destination) {
    $SourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    try { $SourceState = Get-CheckoutState $SourceRoot } catch { $SourceState = $null }
    if ($SourceState -eq 'reuse') {
        $Destination = $SourceRoot
    } else {
        $ProfileDirectory = $env:USERPROFILE
        if (-not $ProfileDirectory) { $ProfileDirectory = [Environment]::GetFolderPath('UserProfile') }
        $Destination = Join-Path $ProfileDirectory 'source\SyncFL-MCP'
    }
}
# Keep relative options anchored to the caller, even when tests later change directory.
$Destination = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Destination)
if ($ClientConfig) {
    $ClientConfig = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ClientConfig)
}
$Action = Get-CheckoutState $Destination
$Installer = Join-Path $Destination 'install.ps1'
$Options = @{ Development = $true; Client = $Client; PythonVersion = $PythonVersion }
if (-not $InstallFlIntegration) { $Options.SkipFlIntegration = $true }
if ($ClientConfig) { $Options.ClientConfig = $ClientConfig }
if ($NoBootstrap) { $Options.NoBootstrap = $true }
if ($Plan) {
    [ordered]@{
        repository = $Repository; destination = $Destination; checkout_action = $Action
        installer = $Installer; installer_options = $Options; run_tests = [bool]$RunTests
        changes_git_history = $false; installs_fl_integration = [bool]$InstallFlIntegration
        writes_client_config = ($Client -ne 'none')
    } | ConvertTo-Json -Depth 4
    return
}
if ($Action -eq 'clone') {
    $Parent = Split-Path -Parent $Destination
    if (-not (Test-Path -LiteralPath $Parent)) {
        New-Item -ItemType Directory -Path $Parent -Force | Out-Null
    }
    Write-Host "Cloning $Repository into $Destination. Git may request your existing GitHub credentials."
    & $Git.Source clone -- $Repository $Destination
    if ($LASTEXITCODE -ne 0) {
        throw 'Git clone failed. Any partial checkout was preserved; resolve the reported error before retrying.'
    }
    if ((Get-CheckoutState $Destination) -ne 'reuse') { throw 'Git clone did not create the expected checkout.' }
} else {
    Write-Host "Using existing checkout: $Destination. Branch, commits and local changes are preserved."
}
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw 'The checkout has no install.ps1. Inspect the existing branch; no update or branch change was attempted.'
}
& $Installer @Options
if ($LASTEXITCODE -ne 0) { throw 'Development installation failed; see the installer output above.' }
if ($RunTests) {
    $Python = Join-Path $Destination '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Checkout Python is missing; tests were not run.' }
    Push-Location $Destination
    try {
        & $Python -m pytest tests agent-harness/cli_anything/flstudio/tests -q
        if ($LASTEXITCODE -ne 0) { throw 'Development tests failed; the checkout was preserved.' }
    } finally { Pop-Location }
}
Write-Host "Developer checkout ready: $Destination"
Write-Host 'No existing Git branch, commit, remote or local change was replaced.'
