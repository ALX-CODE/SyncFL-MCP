param(
    [Parameter(Mandatory = $true)][string]$ImagePath
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

$taskAdapter = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
    $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
if ($null -eq $taskAdapter) { throw 'Windows Runtime task adapter is unavailable' }

function Wait-WinRT($Operation, [Type]$ResultType) {
    $task = $taskAdapter.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    if (-not $task.Wait(8000)) { throw 'Windows OCR operation timed out' }
    return $task.GetAwaiter().GetResult()
}

$ocrStream = $null
$ocrBitmap = $null
try {
    $language = New-Object Windows.Globalization.Language 'en-US'
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
    if ($null -eq $engine) { throw 'Windows English OCR language is not installed' }
    $file = Wait-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync(
        [IO.Path]::GetFullPath($ImagePath))) ([Windows.Storage.StorageFile])
    $ocrStream = Wait-WinRT ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) (
        [Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Wait-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ocrStream)) (
        [Windows.Graphics.Imaging.BitmapDecoder])
    $maxDimension = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    if ($decoder.PixelWidth -gt $maxDimension -or $decoder.PixelHeight -gt $maxDimension) {
        throw "Image exceeds Windows OCR maximum dimension: $maxDimension"
    }
    $ocrBitmap = Wait-WinRT ($decoder.GetSoftwareBitmapAsync()) (
        [Windows.Graphics.Imaging.SoftwareBitmap])
    $recognized = Wait-WinRT ($engine.RecognizeAsync($ocrBitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @()
    foreach ($line in $recognized.Lines) {
        $words = @()
        foreach ($word in $line.Words) {
            $rect = $word.BoundingRect
            $words += @{
                text = $word.Text
                bounds = @([double]$rect.X, [double]$rect.Y,
                    [double]$rect.Width, [double]$rect.Height)
            }
        }
        if ($words.Count -eq 0) { continue }
        $left = ($words | ForEach-Object { $_.bounds[0] } | Measure-Object -Minimum).Minimum
        $top = ($words | ForEach-Object { $_.bounds[1] } | Measure-Object -Minimum).Minimum
        $right = ($words | ForEach-Object { $_.bounds[0] + $_.bounds[2] } | Measure-Object -Maximum).Maximum
        $bottom = ($words | ForEach-Object { $_.bounds[1] + $_.bounds[3] } | Measure-Object -Maximum).Maximum
        $lines += @{
            text = $line.Text
            bounds = @($left, $top, ($right - $left), ($bottom - $top))
            words = @($words)
        }
    }
    $result = @{
        success = $true
        engine = 'Windows.Media.Ocr'
        language = $engine.RecognizerLanguage.LanguageTag
        width = [int]$decoder.PixelWidth
        height = [int]$decoder.PixelHeight
        text = $recognized.Text
        lines = @($lines)
    }
    [Console]::WriteLine(($result | ConvertTo-Json -Depth 8 -Compress))
}
finally {
    if ($null -ne $ocrBitmap) { $ocrBitmap.Dispose() }
    if ($null -ne $ocrStream) { $ocrStream.Dispose() }
}
