param(
    [string]$PptPath = (Join-Path (Split-Path $PSScriptRoot -Parent) '复刻版PPT.pptx'),
    [string]$OutputDir = (Join-Path (Split-Path $PSScriptRoot -Parent) 'rendered_slides')
)

$ErrorActionPreference = 'Stop'
$resolvedPpt = (Resolve-Path -LiteralPath $PptPath).Path
$resolvedRoot = (Resolve-Path -LiteralPath (Split-Path $PSScriptRoot -Parent)).Path
$fullOutput = [System.IO.Path]::GetFullPath($OutputDir)
if (-not $fullOutput.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Output directory must remain inside the workspace: $fullOutput"
}

if (-not (Test-Path -LiteralPath $fullOutput)) {
    New-Item -ItemType Directory -Path $fullOutput | Out-Null
}
Get-ChildItem -LiteralPath $fullOutput -Filter '*.png' -File -ErrorAction SilentlyContinue | Remove-Item -Force

$powerPoint = New-Object -ComObject PowerPoint.Application
$powerPoint.Visible = -1
$presentation = $null
try {
    $presentation = $powerPoint.Presentations.Open($resolvedPpt, $true, $true, $false)
    for ($i = 1; $i -le $presentation.Slides.Count; $i++) {
        $name = '{0:D3}.png' -f $i
        $target = Join-Path $fullOutput $name
        $presentation.Slides.Item($i).Export($target, 'PNG', 592, 333)
        Write-Output $target
    }
}
finally {
    if ($null -ne $presentation) { $presentation.Close() }
    $powerPoint.Quit()
    if ($null -ne $presentation) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($presentation) }
    [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($powerPoint)
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
