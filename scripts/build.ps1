$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Entry = Join-Path $PSScriptRoot "pyinstaller_entry.py"
$DistPath = Join-Path $Root "dist"
$BuildPath = Join-Path $Root "build"
$SpecPath = Join-Path $BuildPath "spec"
$Ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$Ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
$FfmpegRoot = Split-Path -Parent (Split-Path -Parent $Ffmpeg)
$FfmpegLicense = Join-Path $FfmpegRoot "LICENSE"
$FfmpegReadme = Join-Path $FfmpegRoot "README.txt"
$IconSvg = Join-Path $Root "assets\day-distiller.svg"
$IconIco = Join-Path $BuildPath "icon\DayDistiller.ico"

& (Join-Path $PSScriptRoot "ensure-env.ps1") -Dev
& $Python (Join-Path $PSScriptRoot "generate_icon.py") $IconSvg $IconIco

& $Python -m PyInstaller `
  --name "Day Distiller" `
  --windowed `
  --noconfirm `
  --clean `
  --icon $IconIco `
  --paths (Join-Path $Root "src") `
  --distpath $DistPath `
  --workpath $BuildPath `
  --specpath $SpecPath `
  --hidden-import keyring.backends.Windows `
  --hidden-import openai `
  --hidden-import sklearn.ensemble._forest `
  --hidden-import sklearn.tree._classes `
  --hidden-import PySide6.QtSvg `
  --add-binary "$Ffmpeg;resources/ffmpeg" `
  --add-binary "$Ffprobe;resources/ffmpeg" `
  --add-data "$FfmpegLicense;resources/ffmpeg" `
  --add-data "$FfmpegReadme;resources/ffmpeg" `
  --add-data "$IconSvg;assets" `
  $Entry

Write-Host ""
Write-Host "Built EXE:"
Write-Host (Join-Path $DistPath "Day Distiller\Day Distiller.exe")

$PackagePath = Join-Path $DistPath "DayDistiller-production-v2.zip"
if (Test-Path -LiteralPath $PackagePath) {
  Remove-Item -LiteralPath $PackagePath -Force
}
Compress-Archive -LiteralPath (Join-Path $DistPath "Day Distiller") -DestinationPath $PackagePath -CompressionLevel Optimal
Write-Host "Packaged ZIP:"
Write-Host $PackagePath
