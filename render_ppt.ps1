param(
    [string]$PptxPath = '',
    [string]$OutputDir = (Join-Path $PSScriptRoot 'rendered_slides')
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($PptxPath)) {
    $PptxPath = (Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*PPT.pptx' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1).FullName
}
$PptxPath = (Resolve-Path -LiteralPath $PptxPath).Path
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

$powerPoint = New-Object -ComObject PowerPoint.Application
$presentation = $null
try {
    $presentation = $powerPoint.Presentations.Open($PptxPath, $true, $false, $false)
    for ($i = 1; $i -le $presentation.Slides.Count; $i++) {
        $name = '{0:D3}.png' -f $i
        $path = Join-Path $OutputDir $name
        $presentation.Slides.Item($i).Export($path, 'PNG', 592, 333)
    }
}
finally {
    if ($null -ne $presentation) { $presentation.Close() }
    $powerPoint.Quit()
    if ($null -ne $presentation) { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($presentation) | Out-Null }
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($powerPoint) | Out-Null
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
