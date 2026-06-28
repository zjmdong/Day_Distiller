$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Entry = Join-Path $PSScriptRoot "pyinstaller_entry.py"
$DistPath = Join-Path $Root "dist"
$BuildPath = Join-Path $Root "build"
$SpecPath = Join-Path $BuildPath "spec"

& (Join-Path $PSScriptRoot "ensure-env.ps1") -Dev

& $Python -m PyInstaller `
  --name DayDistillerClient `
  --windowed `
  --noconfirm `
  --clean `
  --paths (Join-Path $Root "src") `
  --distpath $DistPath `
  --workpath $BuildPath `
  --specpath $SpecPath `
  --collect-all PySide6 `
  $Entry

Write-Host ""
Write-Host "Built EXE:"
Write-Host (Join-Path $DistPath "DayDistillerClient\DayDistillerClient.exe")
