# SyncFL Windows setup. Run -Plan to preview without changing files or downloading.
[CmdletBinding()]
param(
    [string]$FlUserData,
    [string]$MidiPort,
    [string]$WindowsServer,
    [ValidateSet('none', 'codex', 'claude-desktop', 'claude-code')][string]$Client = 'none',
    [string]$ClientConfig,
    [ValidateSet('3.12', '3.13', '3.14')][string]$PythonVersion = '3.13',
    [switch]$SkipDependencies,
    [switch]$SkipClaude,
    [switch]$NoBootstrap,
    [switch]$Interactive,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
if ($SkipClaude -and $Client -like 'claude-*') { throw '-SkipClaude conflicts with the selected Claude client.' }
if ($ClientConfig -and $Client -eq 'none') { throw '-ClientConfig requires an explicit -Client.' }
if ($Plan) {
    [ordered]@{
        root = $RepoRoot; python = $PythonVersion; client = $Client
        dependencies = @('audio', 'serum'); development_tools = $false
        downloads = -not [bool]$SkipDependencies
        bootstrap_uv_if_missing = -not [bool]($SkipDependencies -or $NoBootstrap)
        fl_user_data = $FlUserData; midi_port = $MidiPort; windows_server = $WindowsServer
        client_config = $ClientConfig; writes_client_config = ($Client -ne 'none')
        fl_installer = 'scripts/install_fl_integration.py'; creates_midi_endpoints = $false
    } | ConvertTo-Json
    return
}
if ($Interactive -and -not $PSBoundParameters.ContainsKey('Client')) {
    Write-Host 'SyncFL setup: 1 = Codex, 2 = Claude Desktop, 3 = Claude Code, 4 = configure client later'
    switch (Read-Host 'Choose your assistant [4]') {
        '1' { $Client = 'codex' }
        '2' { $Client = 'claude-desktop' }
        '3' { $Client = 'claude-code' }
        { $_ -in @('', '4') } { $Client = 'none' }
        default { throw 'Choose 1, 2, 3 or 4.' }
    }
}
function Get-SyncFlUv {
    $Existing = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Existing) { return $Existing.Source }
    if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is missing; install uv manually, then rerun.' }
    $Tools = Join-Path $env:LOCALAPPDATA 'SyncFL\tools'
    $InstalledUv = Join-Path $Tools 'uv.exe'
    if (Test-Path -LiteralPath $InstalledUv -PathType Leaf) { return $InstalledUv }
    if ($NoBootstrap) { throw 'uv is missing. Install it from https://docs.astral.sh/uv/getting-started/installation/ or omit -NoBootstrap.' }
    Write-Host 'Installing uv 0.13.0 for your Windows account (no PATH or shell-profile changes)...'
    $Bootstrap = Join-Path ([IO.Path]::GetTempPath()) ('syncfl-uv-' + [Guid]::NewGuid().ToString('N') + '.ps1')
    $OldInstallDir = $env:UV_INSTALL_DIR
    $OldNoModifyPath = $env:UV_NO_MODIFY_PATH
    try {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/0.13.0/install.ps1' -OutFile $Bootstrap
        $Expected = '6D7EF89BA04838A03D1EBF7F84BE4BA2B98B848A0A9AB8C2855658F13106C2C2'
        if ((Get-FileHash -LiteralPath $Bootstrap -Algorithm SHA256).Hash -ne $Expected) {
            throw 'The uv installer checksum changed. No downloaded code was executed; install uv manually.'
        }
        $env:UV_INSTALL_DIR = $Tools
        $env:UV_NO_MODIFY_PATH = '1'
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Bootstrap -NoModifyPath | Out-Host
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $InstalledUv -PathType Leaf)) {
            throw 'uv installation failed; see the message above.'
        }
        return $InstalledUv
    } finally {
        $env:UV_INSTALL_DIR = $OldInstallDir
        $env:UV_NO_MODIFY_PATH = $OldNoModifyPath
        if (Test-Path -LiteralPath $Bootstrap) { Remove-Item -LiteralPath $Bootstrap }
    }
}
$OldFlData = $env:FL_USER_DATA_FOLDER
$OldMidiPort = $env:FL_MCP_MIDI_PORT
$OldWindowsServer = $env:FL_MCP_WINDOWS_SERVER
$OldUvEnvironment = $env:UV_PROJECT_ENVIRONMENT
# Resolve user paths in the invocation directory, before entering the checkout.
if ($FlUserData) { $FlUserData = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($FlUserData) }
if ($WindowsServer) { $WindowsServer = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($WindowsServer) }
if ($ClientConfig) { $ClientConfig = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ClientConfig) }
Push-Location $RepoRoot
try {
    if ($FlUserData) {
        $FlUserData = [IO.Path]::GetFullPath($FlUserData)
        if (-not (Test-Path -LiteralPath $FlUserData -PathType Container)) { throw 'FlUserData must be the existing Image-Line root containing FL Studio.' }
        $env:FL_USER_DATA_FOLDER = $FlUserData
    }
    if ($MidiPort) { $env:FL_MCP_MIDI_PORT = $MidiPort }
    if ($WindowsServer) {
        $WindowsServer = [IO.Path]::GetFullPath($WindowsServer)
        if (-not (Test-Path -LiteralPath $WindowsServer -PathType Leaf)) { throw 'WindowsServer executable does not exist.' }
        $env:FL_MCP_WINDOWS_SERVER = $WindowsServer
    }
    Write-Host '[1/4] Preparing Python and runtime dependencies...'
    if (-not $SkipDependencies) {
        # An inherited override must never redirect setup into another environment.
        $env:UV_PROJECT_ENVIRONMENT = Join-Path $RepoRoot '.venv'
        $Uv = Get-SyncFlUv
        & $Uv python install $PythonVersion
        if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
        & $Uv sync --project $RepoRoot --locked --no-dev --python $PythonVersion --extra audio --extra serum
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. No controller files were replaced.' }
    }
    $Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Repository Python is missing; rerun without -SkipDependencies.' }
    Write-Host '[2/4] Installing and verifying the FL integration, with backups...'
    $InstallArgs = @((Join-Path $RepoRoot 'scripts\install_fl_integration.py'), '--apply')
    if ($FlUserData) { $InstallArgs += @('--fl-user-data', $FlUserData) }
    $InstallJson = & $Python @InstallArgs
    if ($LASTEXITCODE -ne 0) { throw "FL integration was not installed: $InstallJson" }
    $Installed = $InstallJson | ConvertFrom-Json
    Write-Host "Verified FL integration. Changed files: $($Installed.changed). Backup: $($Installed.backup)"
    & $Python -c 'from fl_studio_mcp.utils.native_midi import verify_runtime; print(verify_runtime())'
    if ($LASTEXITCODE -ne 0) { throw 'Native runtime verification failed; restore the complete source release.' }
    Write-Host "[3/4] Configuring assistant: $Client"
    $Options = @()
    if ($FlUserData) { $Options += @('--fl-user-data', $FlUserData) }
    if ($MidiPort) { $Options += @('--midi-port', $MidiPort) }
    if ($WindowsServer) { $Options += @('--windows-server', $WindowsServer) }
    if ($ClientConfig) { $Options += @('--config', $ClientConfig) }
    if ($Client -eq 'codex') {
        & $Python (Join-Path $RepoRoot 'scripts\register_codex.py') --apply @Options
        if ($LASTEXITCODE -ne 0) { throw 'Codex configuration failed; inspect its preserved configuration before retrying.' }
    } elseif ($Client -like 'claude-*') {
        $ClaudeClient = if ($Client -eq 'claude-code') { 'code' } else { 'desktop' }
        & $Python (Join-Path $RepoRoot 'scripts\register_claude.py') --client $ClaudeClient --apply @Options
        if ($LASTEXITCODE -ne 0) { throw 'Claude configuration failed; inspect its preserved configuration before retrying.' }
    } else {
        Write-Host "Client command: $Python"
        Write-Host 'Client arguments: -m fl_studio_mcp.server'
        Write-Host "Client working directory: $RepoRoot"
    }
    Write-Host '[4/4] Checking the installation without changing the FL session...'
    & $Python (Join-Path $RepoRoot 'scripts\doctor.py')
    if ($LASTEXITCODE -ne 0) { throw 'Installation check needs attention; see the failed checks above.' }
    Write-Host ''
    Write-Host 'SyncFL installed. Keep this folder in place; your assistant points to it.'
    Write-Host 'Next: restart your assistant, start SyncFL, then open FL Studio.'
    Write-Host 'In Options > MIDI settings, enable ALX FL Bridge (FL Studio) and select FL Studio MCP Controller.'
    Write-Host 'Native MIDI needs Windows MIDI Services loopback support and the x64 Visual C++ runtime.'
    Write-Host 'No driver/service installation, elevation, live MIDI send or keyboard/mouse input was performed.'
    Write-Host 'An existing configured MIDI port is an optional fallback via -MidiPort / FL_MCP_MIDI_PORT.'
    Write-Host 'See docs/GETTING-STARTED.md for verification and troubleshooting.'
} finally {
    $env:FL_USER_DATA_FOLDER = $OldFlData
    $env:FL_MCP_MIDI_PORT = $OldMidiPort
    $env:FL_MCP_WINDOWS_SERVER = $OldWindowsServer
    $env:UV_PROJECT_ENVIRONMENT = $OldUvEnvironment
    Pop-Location
}
