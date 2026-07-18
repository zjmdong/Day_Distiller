$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Entry = Join-Path $PSScriptRoot "pyinstaller_entry.py"
$DistPath = Join-Path $Root "dist\pyinstaller"
$WorkPath = Join-Path $Root "build\pyinstaller\work"
$SpecPath = Join-Path $Root "build\pyinstaller\spec"
$IconPng = Join-Path $Root "assets\day-distiller-icon.png"
$IconIco = Join-Path $Root "build\icon\DayDistiller.ico"

& (Join-Path $PSScriptRoot "ensure-env.ps1") -Dev
& $Python (Join-Path $PSScriptRoot "generate_icon.py") $IconPng $IconIco

$Ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$Ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
$FfmpegRoot = Split-Path -Parent (Split-Path -Parent $Ffmpeg)

$Arguments = @(
    "-m", "PyInstaller",
    "--onefile",
    "--windowed",
    "--noconfirm",
    "--clean",
    "--optimize", "1",
    "--name", "Day Distiller",
    "--icon", $IconIco,
    "--paths", (Join-Path $Root "src"),
    "--distpath", $DistPath,
    "--workpath", $WorkPath,
    "--specpath", $SpecPath,
    "--hidden-import", "keyring.backends.Windows",
    "--collect-submodules", "keyring.backends",
    "--exclude-module", "pytest",
    "--exclude-module", "setuptools",
    "--add-data", "$IconPng;assets",
    "--add-binary", "$Ffmpeg;resources\ffmpeg",
    "--add-binary", "$Ffprobe;resources\ffmpeg"
)

foreach ($OptionalFile in @("LICENSE", "README.txt")) {
    $Source = Join-Path $FfmpegRoot $OptionalFile
    if (Test-Path -LiteralPath $Source) {
        $Arguments += @("--add-data", "$Source;resources\ffmpeg")
    }
}

$Arguments += $Entry
& $Python @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$Executable = Join-Path $DistPath "Day Distiller.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "PyInstaller output was not found: $Executable"
}

Write-Host ""
Write-Host "Built single-file Windows application:"
Write-Host $Executable
