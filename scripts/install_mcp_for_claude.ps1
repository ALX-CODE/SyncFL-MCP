# SyncFL Claude configuration: preview by default, write only with -Apply.
[CmdletBinding()]
param(
    [ValidateSet('desktop', 'code')][string]$Client = 'desktop',
    [string]$Config,
    [switch]$Apply,
    [switch]$Replace
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Interpreter = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Interpreter -PathType Leaf)) {
    throw 'Create the repository environment with uv sync before configuring a client.'
}
$Arguments = @((Join-Path $PSScriptRoot 'register_claude.py'), '--client', $Client)
if ($Config) { $Arguments += @('--config', $Config) }
if ($Apply) { $Arguments += '--apply' }
if ($Replace) { $Arguments += '--replace' }
& $Interpreter @Arguments
if ($LASTEXITCODE -ne 0) { throw 'SyncFL client configuration failed; the original was preserved.' }
if (-not $Apply) { Write-Host 'Preview only. Rerun with -Apply after reviewing the entry.' }
