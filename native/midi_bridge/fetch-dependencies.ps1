[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$deps = Join-Path $PSScriptRoot 'build\deps'
New-Item -ItemType Directory -Path $deps -Force | Out-Null
$packages = @(
    @{
        Url = 'https://github.com/microsoft/MIDI/releases/download/inbox-preview-11/Windows.Devices.Midi2.0.99.92-preview.11.nupkg'
        File = 'Windows.Devices.Midi2.0.99.92-preview.11.zip'
        Folder = 'midi-sdk'
        Hash = '13AFC6E6DA2ADAC309AB8C174352F46F1C0EBE2D79E65475BD6274DEFB73DF74'
    },
    @{
        Url = 'https://api.nuget.org/v3-flatcontainer/microsoft.windows.cppwinrt/3.0.260520.1/microsoft.windows.cppwinrt.3.0.260520.1.nupkg'
        File = 'cppwinrt.zip'
        Folder = 'cppwinrt'
        Hash = 'D22E2E26133D63217AE26E91B1685FB024B03A508A78AF645F8347A3126C8435'
    }
)
Write-Host 'Pinned Microsoft SDK build dependencies: about 99 MiB download, no system installation.'
foreach ($package in $packages) {
    $archive = Join-Path $deps $package.File
    if (-not (Test-Path -LiteralPath $archive)) { Invoke-WebRequest -Uri $package.Url -OutFile $archive }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $package.Hash) {
        throw "Dependency checksum mismatch: $archive. Remove that archive explicitly and retry."
    }
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $deps $package.Folder) -Force
}
& (Join-Path $deps 'cppwinrt\bin\cppwinrt.exe') -input sdk -input (Join-Path $deps 'midi-sdk\ref\native\Windows.Devices.Midi2.winmd') -output (Join-Path $deps 'generated')
if ($LASTEXITCODE -ne 0) { throw 'C++/WinRT projection generation failed. Install a Windows SDK with metadata.' }
