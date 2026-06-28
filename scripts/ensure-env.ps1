param(
    [switch]$Dev
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.11 -m venv $Venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv $Venv
    } else {
        throw "Python 3.11 was not found. Install Python 3.11 first."
    }
}

& $Python -m pip install --disable-pip-version-check -U pip

if ($Dev) {
    $Target = "$Root[dev]"
} else {
    $Target = $Root
}

& $Python -m pip install --disable-pip-version-check -e $Target
