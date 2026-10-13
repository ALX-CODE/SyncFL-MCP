[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'verify-runtime.ps1')
$runtime = Join-Path $PSScriptRoot 'runtime'
$manifest = Get-Content -LiteralPath (Join-Path $runtime 'manifest.json') -Raw | ConvertFrom-Json
$files = @($manifest.files.PSObject.Properties.Name) + @('manifest.json')
$paths = @($files | ForEach-Object { Join-Path $runtime $_ })
$archive = Join-Path $PSScriptRoot 'build\alx-midi-service-0.1.0-win-x64.zip'
Compress-Archive -LiteralPath $paths -DestinationPath $archive -Force
Get-FileHash -LiteralPath $archive -Algorithm SHA256
