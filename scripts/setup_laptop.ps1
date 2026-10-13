# Compatibility entry point. All Windows setup now uses the guarded root installer.
[CmdletBinding()]
param(
    [string]$FlUserData,
    [string]$MidiPort,
    [string]$WindowsServer,
    [string]$CodexConfig,
    [ValidateSet('3.12', '3.13', '3.14')][string]$PythonVersion = '3.13',
    [switch]$SkipCodex,
    [switch]$SkipDependencies,
    [switch]$NoBootstrap,
    [switch]$Plan
)
$Options = @{ PythonVersion = $PythonVersion; Client = 'codex' }
if ($SkipCodex) { $Options.Client = 'none' }
if ($FlUserData) { $Options.FlUserData = $FlUserData }
if ($MidiPort) { $Options.MidiPort = $MidiPort }
if ($WindowsServer) { $Options.WindowsServer = $WindowsServer }
if ($CodexConfig -and -not $SkipCodex) { $Options.ClientConfig = $CodexConfig }
if ($SkipDependencies) { $Options.SkipDependencies = $true }
if ($NoBootstrap) { $Options.NoBootstrap = $true }
if ($Plan) { $Options.Plan = $true }
& (Join-Path $PSScriptRoot '..\install.ps1') @Options
