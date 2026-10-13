[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'build\Release'
$runtime = Join-Path $PSScriptRoot 'runtime'
$binaries = @('alx-midi-service.exe', 'Windows.Devices.Midi2.dll', 'Windows.Devices.Midi2.pri')
foreach ($name in $binaries) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $name))) { throw "Missing Release build: $name. Run build.ps1 -FetchDependencies first." }
}
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
foreach ($name in $binaries) { Copy-Item -LiteralPath (Join-Path $source $name) -Destination (Join-Path $runtime $name) -Force }
foreach ($name in @('README.md', 'THIRD-PARTY-NOTICES.md')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $runtime $name) -Force
}
$hashes = [ordered]@{}
foreach ($name in ($binaries + @('README.md', 'THIRD-PARTY-NOTICES.md'))) {
    $hashes[$name] = [ordered]@{
        sha256 = (Get-FileHash -LiteralPath (Join-Path $runtime $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        size = (Get-Item -LiteralPath (Join-Path $runtime $name)).Length
    }
}
$manifest = [ordered]@{
    schema_version = 1
    architecture = 'x64'
    helper_version = '0.1.0'
    sdk_version = '0.99.92-preview.11'
    sdk_source = 'https://github.com/microsoft/MIDI/releases/download/inbox-preview-11/Windows.Devices.Midi2.0.99.92-preview.11.nupkg'
    sdk_package_sha256 = '13afc6e6da2adac309ab8c174352f46f1c0ebe2d79e65475bd6274defb73df74'
    output_name = 'ALX FL Bridge (Send)'
    input_name = 'ALX FL Bridge (FL Studio)'
    files = $hashes
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runtime 'manifest.json') -Encoding utf8
& (Join-Path $PSScriptRoot 'verify-runtime.ps1') -RuntimePath $runtime -Probe
