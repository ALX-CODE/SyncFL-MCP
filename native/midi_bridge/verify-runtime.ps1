[CmdletBinding()]
param([string]$RuntimePath = (Join-Path $PSScriptRoot 'runtime'), [switch]$Probe)
$ErrorActionPreference = 'Stop'
$manifest = Get-Content -LiteralPath (Join-Path $RuntimePath 'manifest.json') -Raw | ConvertFrom-Json
if ($manifest.schema_version -ne 1 -or $manifest.architecture -ne 'x64' -or $manifest.sdk_version -ne '0.99.92-preview.11') {
    throw 'Unexpected runtime manifest schema, architecture or SDK version.'
}
$expected = @('alx-midi-service.exe','Windows.Devices.Midi2.dll','Windows.Devices.Midi2.pri','README.md','THIRD-PARTY-NOTICES.md')
$actual = @($manifest.files.PSObject.Properties.Name)
if (Compare-Object ($expected | Sort-Object) ($actual | Sort-Object)) { throw 'Unexpected runtime file list.' }
foreach ($name in $expected) {
    $hash = (Get-FileHash -LiteralPath (Join-Path $RuntimePath $name) -Algorithm SHA256).Hash
    if ($hash -ne $manifest.files.$name.sha256) { throw "Runtime checksum mismatch: $name" }
    if ((Get-Item -LiteralPath (Join-Path $RuntimePath $name)).Length -ne $manifest.files.$name.size) { throw "Runtime size mismatch: $name" }
}
foreach ($name in @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path ([Environment]::SystemDirectory) $name))) {
        throw "Missing x64 Visual C++ runtime: $name. Install Microsoft's supported x64 VC++ redistributable."
    }
}
Write-Host 'Verified 5 runtime files and x64 Visual C++ runtime presence.'
if ($Probe) {
    & (Join-Path $RuntimePath 'alx-midi-service.exe') status --json
    if ($LASTEXITCODE -ne 0) { throw "Runtime status probe failed with exit $LASTEXITCODE. No endpoints were created." }
}
