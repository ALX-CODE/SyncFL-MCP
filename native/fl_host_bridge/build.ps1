[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$SdkRoot,
    [ValidateSet('Release','Debug')][string]$Configuration = 'Release'
)
$ErrorActionPreference = 'Stop'
$sdkPath = (Resolve-Path -LiteralPath $SdkRoot).Path
$buildPath = Join-Path $PSScriptRoot 'build'
& cmake -S $PSScriptRoot -B $buildPath -G 'Visual Studio 18 2026' -A x64 "-DFL_SDK_ROOT=$sdkPath"
if ($LASTEXITCODE -ne 0) { throw 'CMake configure failed' }
& cmake --build $buildPath --config $Configuration --parallel
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
& ctest --test-dir $buildPath -C $Configuration --output-on-failure
if ($LASTEXITCODE -ne 0) { throw 'Fake-host tests failed' }
