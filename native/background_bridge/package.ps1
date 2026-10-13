param([string]$Configuration = 'Release')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$destination = Join-Path $root 'runtime'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$entries = [ordered]@{}
foreach ($name in @('alx-background-bridge.exe', 'alx-background-hook.dll')) {
    $source = Join-Path (Join-Path (Join-Path $root 'build') $Configuration) $name
    $target = Join-Path $destination $name
    Copy-Item -LiteralPath $source -Destination $target -Force
    $entries[$name] = [ordered]@{
        sha256 = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        size = (Get-Item -LiteralPath $target).Length
    }
}
$manifest = [ordered]@{
    schema_version = 1
    architecture = 'x64'
    experimental = $true
    files = $entries
}
$manifestPath = Join-Path $destination 'manifest.json'
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 5),
                       [Text.UTF8Encoding]::new($false))
$pin = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Output "Manifest SHA256 (update and review Python MANIFEST_SHA256): $pin"
