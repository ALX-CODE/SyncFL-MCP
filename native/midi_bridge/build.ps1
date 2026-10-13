[CmdletBinding()]
param(
    [ValidateSet('Release','Debug')][string]$Configuration = 'Release',
    [switch]$FetchDependencies
)
$ErrorActionPreference = 'Stop'
$buildPath = Join-Path $PSScriptRoot 'build'
if ($FetchDependencies) { & (Join-Path $PSScriptRoot 'fetch-dependencies.ps1') }
if (-not (Test-Path -LiteralPath (Join-Path $buildPath 'deps\generated\winrt\Windows.Devices.Midi2.h'))) {
    throw 'SDK dependencies are missing. Run build.ps1 -FetchDependencies (99 MiB download; no system install).'
}
& cmake -S $PSScriptRoot -B $buildPath -G 'Visual Studio 18 2026' -A x64
if ($LASTEXITCODE -ne 0) { throw 'CMake configure failed' }
& cmake --build $buildPath --config $Configuration --parallel
if ($LASTEXITCODE -ne 0) { throw 'CMake build failed' }
& ctest --test-dir $buildPath -C $Configuration --output-on-failure
if ($LASTEXITCODE -ne 0) { throw 'Native bridge tests failed' }
