param(
    [switch]$Dev,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$ExtraArgs = @()
    )

    try {
        $commandArgs = @()
        $commandArgs += $ExtraArgs
        $commandArgs += @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)")
        & $Exe @commandArgs *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function New-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$ExtraArgs = @()
    )

    [PSCustomObject]@{
        Exe = $Exe
        ExtraArgs = $ExtraArgs
    }
}

function Find-PythonCandidate {
    $candidates = @()

    if ($env:DAY_DISTILLER_PYTHON) {
        $candidates += New-PythonCandidate -Exe $env:DAY_DISTILLER_PYTHON
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += New-PythonCandidate -Exe "py" -ExtraArgs @("-3.11")
    }
    $espPythonRoot = "D:\ESP-IDF\.espressif\python_env"
    if (Test-Path -LiteralPath $espPythonRoot) {
        $espPythons = Get-ChildItem -Path $espPythonRoot -Recurse -Filter python.exe -ErrorAction SilentlyContinue |
            Sort-Object -Property FullName -Descending
        foreach ($item in $espPythons) {
            $candidates += New-PythonCandidate -Exe $item.FullName
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $candidates += New-PythonCandidate -Exe "python"
    }
    if (Get-Command python3 -ErrorAction SilentlyContinue) {
        $candidates += New-PythonCandidate -Exe "python3"
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += New-PythonCandidate -Exe "py" -ExtraArgs @("-3")
    }

    foreach ($candidate in $candidates) {
        if (Test-PythonCandidate -Exe $candidate.Exe -ExtraArgs $candidate.ExtraArgs) {
            return $candidate
        }
    }

    return $null
}

if (-not (Test-Path -LiteralPath $Python)) {
    if (Test-Path -LiteralPath $Venv) {
        $resolvedVenv = (Resolve-Path -LiteralPath $Venv).Path
        if (-not $resolvedVenv.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove unexpected venv path: $resolvedVenv"
        }
        Remove-Item -LiteralPath $Venv -Recurse -Force
    }

    $candidate = Find-PythonCandidate
    if ($null -eq $candidate) {
        throw "Python 3.11+ was not found. Install Python 3.11, or set DAY_DISTILLER_PYTHON to a python.exe path."
    }

    Write-Host "Using Python runtime: $($candidate.Exe) $($candidate.ExtraArgs -join ' ')"
    $venvArgs = @()
    $venvArgs += $candidate.ExtraArgs
    $venvArgs += @("-m", "venv", $Venv)
    & $candidate.Exe @venvArgs
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Python)) {
        throw "Failed to create virtual environment at $Venv"
    }
}

if ($SkipInstall) {
    & $Python --version
    return
}

& $Python -m pip install --disable-pip-version-check -U pip

if ($Dev) {
    $Target = "$Root[dev]"
} else {
    $Target = $Root
}

& $Python -m pip install --disable-pip-version-check -e $Target
