# FL Studio MCP installation for Windows. No elevation or service/driver installation.
[CmdletBinding()]
param(
    [string]$FlUserData,
    [switch]$SkipDependencies,
    [switch]$SkipClaude
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    Write-Host '[1/4] Checking Python environment...'
    if (-not $SkipDependencies) {
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            throw 'Install uv first (https://docs.astral.sh/uv/getting-started/installation/), then rerun.'
        }
        & uv python install 3.12
        if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
        & uv sync --locked --python 3.12
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
    $Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw 'Repository Python is missing; rerun without -SkipDependencies.'
    }

    Write-Host '[2/4] Installing and verifying the complete FL controller and piano files...'
    $InstallArgs = @((Join-Path $PSScriptRoot 'scripts\install_fl_integration.py'), '--apply')
    if ($FlUserData) { $InstallArgs += @('--fl-user-data', $FlUserData) }
    $InstallJson = & $Python @InstallArgs
    if ($LASTEXITCODE -ne 0) { throw "FL file installation failed: $InstallJson" }
    $Installed = $InstallJson | ConvertFrom-Json
    Write-Host "Changed files: $($Installed.changed); backup: $($Installed.backup)"
    Write-Host "Verified controller digest: $($Installed.installed_controller_sha256)"

    Write-Host '[3/4] Verifying the bundled native MIDI runtime (offline)...'
    & $Python -c 'from fl_studio_mcp.utils.native_midi import verify_runtime; print(verify_runtime())'
    if ($LASTEXITCODE -ne 0) { throw 'Native MIDI runtime failed verification; restore the complete checkout.' }

    if (-not $SkipClaude) {
        Write-Host '[4/4] Running the existing Claude MCP configuration helper...'
        & (Join-Path $PSScriptRoot 'scripts\install_mcp_for_claude.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Claude configuration failed.' }
    }

    Write-Host ''
    Write-Host 'FL files verified. Reload the controller or restart FL after current work finishes.'
    Write-Host 'Native MIDI is the Windows default: start the MCP server before opening FL.'
    Write-Host 'The server creates/reuses ALX FL Bridge (Send) and ALX FL Bridge (FL Studio), then its helper exits.'
    Write-Host 'In FL MIDI inputs, enable ALX FL Bridge (FL Studio) and select FL Studio MCP Controller.'
    Write-Host 'Windows MIDI Services loopback support and the x64 Visual C++ runtime are required.'
    Write-Host 'This installer does not create endpoints, change services, install drivers, elevate, or stop processes.'
    Write-Host 'If native MIDI is unavailable, configure an existing alternative output with FL_MCP_MIDI_PORT.'
    Write-Host 'For example, a configured loopMIDI port remains an optional fallback and needs loopMIDI running.'
    Write-Host 'See native/midi_bridge/README.md and docs/LAPTOP-SETUP.md for prerequisites and client setup.'
    Write-Host 'For manual server startup: uv run fl-studio-mcp'
} finally {
    Pop-Location
}
