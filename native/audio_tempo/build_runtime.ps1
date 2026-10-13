[CmdletBinding()]
param(
    [string]$TargetDirectory = ''
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
if (-not $TargetDirectory) {
    $TargetDirectory = Join-Path $repositoryRoot 'build/audio_tempo'
}
$resolvedTarget = [System.IO.Path]::GetFullPath($TargetDirectory)
$cargoCommand = Get-Command cargo -ErrorAction Stop
$previousTarget = $env:CARGO_TARGET_DIR
$previousEncodedFlags = $env:CARGO_ENCODED_RUSTFLAGS
$previousRustFlags = $env:RUSTFLAGS

try {
    $env:CARGO_TARGET_DIR = $resolvedTarget
    $remaps = @(
        "$repositoryRoot=/syncfl",
        "$(Join-Path $env:USERPROFILE '.cargo')=/cargo",
        "$(Join-Path $env:USERPROFILE '.rustup')=/rustup"
    )
    # Rust diagnostics and codegen may use either separator style on Windows.
    $flags = foreach ($mapping in $remaps) {
        '--remap-path-prefix=' + $mapping
        '--remap-path-prefix=' + $mapping.Replace('\', '/')
    }
    # Encoded arguments preserve repository/user paths containing spaces.
    # This script intentionally uses its own known build flags; it restores any
    # previous encoded flags after building.
    $env:CARGO_ENCODED_RUSTFLAGS = $flags -join [char]31
    $env:RUSTFLAGS = $null
    & $cargoCommand.Source build --manifest-path (Join-Path $PSScriptRoot 'Cargo.toml') --release --locked
    if ($LASTEXITCODE -ne 0) { throw "Cargo build failed with exit code $LASTEXITCODE" }
    Write-Output (Join-Path $resolvedTarget 'release/syncfl-tempo.exe')
}
finally {
    $env:CARGO_TARGET_DIR = $previousTarget
    $env:CARGO_ENCODED_RUSTFLAGS = $previousEncodedFlags
    $env:RUSTFLAGS = $previousRustFlags
}
