$ErrorActionPreference = "Stop"

python -m PyInstaller `
  --name DayDistillerClient `
  --windowed `
  --noconfirm `
  --clean `
  --collect-all PySide6 `
  src\day_distiller_client\__main__.py
