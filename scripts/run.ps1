$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

& (Join-Path $PSScriptRoot "ensure-env.ps1")
& $Python -m day_distiller_client
