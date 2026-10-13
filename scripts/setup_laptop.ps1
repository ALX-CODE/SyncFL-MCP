# Install this checkout into Codex and FL Studio; no machine execution-policy changes.
[CmdletBinding()]
param(
    [string]$FlUserData,
    [string]$MidiPort,
    [string]$WindowsServer,
    [string]$CodexConfig,
    [ValidateSet('3.12', '3.13', '3.14')][string]$PythonVersion = '3.13',
    [switch]$SkipCodex,
    [switch]$SkipDependencies
)
$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$OldFlData = $env:FL_USER_DATA_FOLDER
$OldWindowsServer = $env:FL_MCP_WINDOWS_SERVER
$OldMidiPort = $env:FL_MCP_MIDI_PORT
$BridgeLocks = @()
Push-Location $RepoRoot
try {
    if (-not $SkipDependencies -and -not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'Install uv first: winget install --id astral-sh.uv -e, then reopen PowerShell.'
    }
    if ($FlUserData) {
        $FlUserData = [IO.Path]::GetFullPath($FlUserData)
        if (-not (Test-Path -LiteralPath $FlUserData -PathType Container)) {
            throw 'FlUserData must be the existing Image-Line user data root selected in FL settings.'
        }
        $env:FL_USER_DATA_FOLDER = $FlUserData
    }
    if ($WindowsServer) {
        $WindowsServer = [IO.Path]::GetFullPath($WindowsServer)
        if (-not (Test-Path -LiteralPath $WindowsServer -PathType Leaf)) {
            throw 'WindowsServer executable does not exist.'
        }
        $env:FL_MCP_WINDOWS_SERVER = $WindowsServer
    }
    if (-not $SkipDependencies) {
        & uv python install $PythonVersion
        if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
        & uv sync --locked --python $PythonVersion --extra serum --extra audio --extra dev
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; lockfile may need updating.' }
    }
    $Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw 'Repository Python is missing; rerun without -SkipDependencies.'
    }
    # An explicit alternative remains supported; normal installs own their endpoint.
    if (-not $MidiPort) { $MidiPort = 'ALX FL Bridge (Send)' }
    $env:FL_MCP_MIDI_PORT = $MidiPort
    $MidiJson = & $Python -c 'import json; from fl_studio_mcp.utils.native_midi import initialize_native_midi; r = initialize_native_midi(); print(json.dumps(r)); raise SystemExit(0 if r["success"] else 1)'
    $MidiExit = $LASTEXITCODE
    $Midi = $MidiJson | ConvertFrom-Json
    if ($MidiExit -ne 0) {
        throw "Native MIDI setup failed: $($Midi.error). See native/midi_bridge/README.md, or explicitly select an existing alternative with -MidiPort."
    }
    $PathsJson = & $Python (Join-Path $PSScriptRoot 'doctor.py') --paths-only
    if ($LASTEXITCODE -ne 0) { throw 'FL path discovery failed.' }
    $Paths = $PathsJson | ConvertFrom-Json
    $Settings = [IO.Path]::GetFullPath($Paths.settings)
    if (-not (Test-Path -LiteralPath $Settings -PathType Container)) {
        throw 'FL Settings folder not found. Launch FL once or pass -FlUserData with its actual root.'
    }
    $Piano = Join-Path $Settings 'Piano roll scripts'
    # The lock files persist; lock ownership, not file existence, means busy.
    foreach ($LockName in @('Hardware\FLStudioMCP\mcp.lock', 'Piano roll scripts\pattern-bank.lock')) {
        $LockPath = Join-Path $Settings $LockName
        New-Item -ItemType Directory -Path (Split-Path $LockPath -Parent) -Force | Out-Null
        $Stream = [IO.File]::Open($LockPath, [IO.FileMode]::OpenOrCreate,
                                [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
        try {
            $Stream.Lock(0, 1)
            $BridgeLocks += $Stream
        } catch {
            $Stream.Dispose()
            throw 'An FL bridge client is active. Wait for it to finish before upgrading.'
        }
    }
    foreach ($Guard in @('mcp-verification.json')) {
        if (Test-Path -LiteralPath (Join-Path $Piano $Guard)) {
            throw "Piano bridge has $Guard. Resolve pending work before upgrading."
        }
    }
    $Queue = Join-Path $Piano 'mcp_request.json'
    if ((Test-Path -LiteralPath $Queue) -and ((Get-Content -LiteralPath $Queue -Raw).Trim() -ne '[]')) {
        throw 'Pending piano request found. Resolve it before upgrading.'
    }
    $Deployment = @(
        @('fl_controller\device_FLStudioMCP.py', 'Hardware\FLStudioMCP\device_FLStudioMCP.py'),
        @('fl_controller\mcp_extended.py', 'Hardware\FLStudioMCP\mcp_extended.py'),
        @('fl_controller\mcp_production.py', 'Hardware\FLStudioMCP\mcp_production.py'),
        @('fl_controller\native_catalog.json', 'Hardware\FLStudioMCP\native_catalog.json'),
        @('scripts\ComposeWithLLM.pyscript', 'Piano roll scripts\ComposeWithLLM.pyscript'),
        @('src\fl_studio_mcp\utils\note_protocol.py', 'Piano roll scripts\mcp_note_protocol.py')
    )
    $Backup = Join-Path $Settings ('MCP-install-backups\' + [Guid]::NewGuid().ToString('N'))
    $Changed = 0
    foreach ($Pair in $Deployment) {
        $Source = Join-Path $RepoRoot $Pair[0]
        $Target = [IO.Path]::GetFullPath((Join-Path $Settings $Pair[1]))
        if (-not $Target.StartsWith($Settings.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Deployment target escaped FL Settings.'
        }
        if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) { throw "Missing source: $($Pair[0])" }
        if ((Test-Path -LiteralPath $Target) -and
            ((Get-FileHash -LiteralPath $Source).Hash -eq (Get-FileHash -LiteralPath $Target).Hash)) {
            continue
        }
        if (Test-Path -LiteralPath $Target) {
            $Saved = Join-Path $Backup $Pair[1]
            New-Item -ItemType Directory -Path (Split-Path $Saved -Parent) -Force | Out-Null
            Copy-Item -LiteralPath $Target -Destination $Saved
        }
        New-Item -ItemType Directory -Path (Split-Path $Target -Parent) -Force | Out-Null
        $Temporary = $Target + '.installing-' + [Guid]::NewGuid().ToString('N')
        Copy-Item -LiteralPath $Source -Destination $Temporary
        Move-Item -LiteralPath $Temporary -Destination $Target -Force
        $Changed++
    }
    if (-not $SkipCodex) {
        $Registration = @((Join-Path $PSScriptRoot 'register_codex.py'), '--apply',
                          '--fl-user-data', $Paths.fl_user_data)
        if ($MidiPort) { $Registration += @('--midi-port', $MidiPort) }
        if ($Paths.windows_server) { $Registration += @('--windows-server', $Paths.windows_server) }
        if ($CodexConfig) { $Registration += @('--config', $CodexConfig) }
        & $Python @Registration
        if ($LASTEXITCODE -ne 0) { throw 'Codex registration failed.' }
    }
    & $Python (Join-Path $PSScriptRoot 'doctor.py')
    if ($LASTEXITCODE -ne 0) { throw 'Offline installation audit failed.' }
    Write-Host "Installed: $Changed changed FL files. Prior files retained under $Backup when needed."
    Write-Host 'Restart Codex and reload the FL MIDI controller before using upgraded tools.'
    if ($Midi.enabled) {
        Write-Host 'Enable ALX FL Bridge (FL Studio) in FL MIDI inputs and assign FL Studio MCP Controller. No loopMIDI process is needed.'
    } else {
        Write-Host "Using explicitly selected MIDI output: $MidiPort. Configure its receiving input in FL."
    }
    Write-Host 'fl_piano_roll_prepare selects ComposeWithLLM automatically. See docs/LAPTOP-SETUP.md.'
    Write-Host 'No live MIDI, GUI, audio, or laptop playback test was performed by this installer.'
} finally {
    foreach ($Stream in $BridgeLocks) {
        try { $Stream.Unlock(0, 1) } finally { $Stream.Dispose() }
    }
    $env:FL_USER_DATA_FOLDER = $OldFlData
    $env:FL_MCP_WINDOWS_SERVER = $OldWindowsServer
    $env:FL_MCP_MIDI_PORT = $OldMidiPort
    Pop-Location
}
