# Day Distiller Windows Client

Windows Python prototype for the Day Distiller USB Link maintenance interface.

This branch intentionally contains only the host client. Firmware changes live
on the `usb-link-main` branch.

## Features

- Scan serial ports for the Day Distiller protocol CDC interface.
- Perform `HELLO`, `GET_STATUS`, `ENTER_MSC`, and `EXIT_MSC`.
- Detect newly mounted removable drives on Windows.
- Best-effort safe eject through the Windows Shell COM API.
- PySide6 UI for connection state, TF card status, access mode, entering MSC,
  ejecting, and exiting back to serial mode.

## Install

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .[dev]
```

## Run

```powershell
python -m day_distiller_client
```

## Test

```powershell
python -m unittest discover -s tests
```

## Build EXE

```powershell
.\scripts\build.ps1
```

## Protocol

The client implements USB Link protocol version 1:

- SLIP framed binary header plus JSON payload.
- CRC32 over header and payload.
- Command CDC is the second CDC interface in serial mode and the only CDC
  interface in MSC mode.
- `ENTER_MSC` reboots the device into CDC + MSC mode.
- `EXIT_MSC` should be sent only after the Windows volume is safely ejected,
  unless the user explicitly forces exit.
