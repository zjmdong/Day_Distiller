# Day Distiller · Historical Windows USB client

**English** · [简体中文](README.zh-CN.md)

> [!WARNING]
> **Historical development branch: `old/windows-dev-client-py`.** This is the early Windows USB maintenance utility, **not** the current Daily Journal application. Use [`desktop-app`](https://github.com/zjmdong/Day_Distiller/tree/desktop-app) for the current experience and [`firmware-production`](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) for the project overview.

## Snapshot state

Reviewed **2026-09-20**, code baseline **`fba0cb2`** (`Improve Python runtime detection for client scripts`), package version **0.1.0**.

Implemented here:

- A Python / PySide6 **Windows** interface for serial discovery and connection status.
- `HELLO`, `GET_STATUS`, `ENTER_MSC` and `EXIT_MSC` USB operations.
- Removable-drive discovery, read-only/read-write selection and best-effort Windows Shell eject.
- Basic protocol tests and a historical PyInstaller build script.

Not implemented here: the current AI journal pipeline, media analysis, poster generation, recoverable SQLite jobs, modern export transactions, firmware-2.1 settings or the Apple Silicon macOS workflow.

## Run this snapshot

Only use this branch to study or reproduce the historical utility. On Windows with Python 3.11:

```powershell
git clone --branch old/windows-dev-client-py https://github.com/zjmdong/Day_Distiller.git Day_Distiller_Legacy_Client
cd Day_Distiller_Legacy_Client
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m day_distiller_client
.\.venv\Scripts\python.exe -m pytest -q
```

`RunClient.bat` / `scripts/run.ps1` are convenience launchers. If using an existing interpreter, set `DAY_DISTILLER_PYTHON` to **your own** Python path. Do not copy a developer's ESP-IDF installation path.

For the historical executable build, run `scripts/build.ps1`; the output is `dist/DayDistillerClient/DayDistillerClient.exe`. This is a legacy PyInstaller path, not the Nuitka packaging used by the current application.

## Device workflow and limitations

The historical firmware counterpart is [`old/usb-link-dev-firmware`](https://github.com/zjmdong/Day_Distiller/tree/old/usb-link-dev-firmware).

1. Use a backed-up test card and a data-capable USB cable.
2. Auto-discover the device, confirm the serial connection and select **Read Only** initially.
3. Enter MSC; wait for USB re-enumeration before accessing files.
4. Finish file operations, confirm host ejection and then leave MSC.

Safe eject is **best effort** in this snapshot. Do not treat a button press as proof that all filesystem writes have completed. Do not force exit or unplug during writes. Modern devices may expose additional features that this client neither understands nor protects.

**JerryZ** independently developed Day Distiller. This historical branch does not add a project-wide open-source license; see the [main project](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) for current scope and licensing.
