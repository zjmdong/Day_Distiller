$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Entry = Join-Path $PSScriptRoot "nuitka_entry.py"
$DistPath = Join-Path $Root "dist"
$BuildPath = Join-Path $Root "build\nuitka\windows"
$BundlePath = Join-Path $DistPath "Day Distiller"
$PackagePath = Join-Path $DistPath "DayDistiller-Windows-x64.zip"
$Ffmpeg = (Get-Command ffmpeg -ErrorAction Stop).Source
$Ffprobe = (Get-Command ffprobe -ErrorAction Stop).Source
$FfmpegRoot = Split-Path -Parent (Split-Path -Parent $Ffmpeg)
$FfmpegLicense = Join-Path $FfmpegRoot "LICENSE"
$FfmpegReadme = Join-Path $FfmpegRoot "README.txt"
$IconPng = Join-Path $Root "assets\day-distiller-icon.png"
$IconIco = Join-Path $Root "build\icon\DayDistiller.ico"

& (Join-Path $PSScriptRoot "ensure-env.ps1") -Dev
& $Python (Join-Path $PSScriptRoot "generate_icon.py") $IconPng $IconIco

$NuitkaArgs = @(
    "-m", "nuitka",
    "--mode=standalone",
    "--enable-plugin=pyside6",
    "--windows-console-mode=disable",
    "--windows-icon-from-ico=$IconIco",
    "--windows-product-name=Day Distiller",
    "--windows-file-description=Day Distiller",
    "--windows-company-name=Day Distiller",
    "--file-version=0.3.0.0",
    "--product-version=0.3.0.0",
    "--output-filename=Day Distiller.exe",
    "--output-dir=$BuildPath",
    "--mingw64",
    "--assume-yes-for-downloads",
    "--lto=yes",
    "--python-flag=no_docstrings",
    "--noinclude-pytest-mode=nofollow",
    "--noinclude-setuptools-mode=nofollow",
    "--include-package=keyring.backends.Windows",
    "--include-data-files=$IconPng=assets/day-distiller-icon.png",
    "--include-data-files=$Ffmpeg=resources/ffmpeg/ffmpeg.exe",
    "--include-data-files=$Ffprobe=resources/ffmpeg/ffprobe.exe",
    "--report=$(Join-Path $BuildPath 'nuitka-report.xml')"
)
if (Test-Path -LiteralPath $FfmpegLicense) {
    $NuitkaArgs += "--include-data-files=$FfmpegLicense=resources/ffmpeg/LICENSE"
}
if (Test-Path -LiteralPath $FfmpegReadme) {
    $NuitkaArgs += "--include-data-files=$FfmpegReadme=resources/ffmpeg/README.txt"
}
$NuitkaArgs += $Entry
& $Python @NuitkaArgs
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka build failed with exit code $LASTEXITCODE"
}

$CompiledBundle = Join-Path $BuildPath "nuitka_entry.dist"
if (-not (Test-Path -LiteralPath $CompiledBundle)) {
    throw "Nuitka output directory was not found: $CompiledBundle"
}
if (Test-Path -LiteralPath $BundlePath) {
    $resolved = (Resolve-Path -LiteralPath $BundlePath).Path
    if (-not $resolved.StartsWith($DistPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove unexpected bundle path: $resolved"
    }
    Remove-Item -LiteralPath $BundlePath -Recurse -Force
}
Copy-Item -LiteralPath $CompiledBundle -Destination $BundlePath -Recurse
if (Test-Path -LiteralPath $PackagePath) {
    Remove-Item -LiteralPath $PackagePath -Force
}
Compress-Archive -LiteralPath $BundlePath -DestinationPath $PackagePath -CompressionLevel Optimal

Write-Host ""
Write-Host "Built Nuitka application:"
Write-Host (Join-Path $BundlePath "Day Distiller.exe")
Write-Host "Packaged ZIP:"
Write-Host $PackagePath
