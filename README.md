# Day Distiller Windows Client

Day Distiller USB Link Windows upper-computer client.

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

Double-click `RunClient.bat`, or run:

```powershell
.\scripts\run.ps1
```

The first launch creates `.venv` and installs Python dependencies. This can
take a few minutes. The script tries Python 3.11 from the system first, then
falls back to the ESP-IDF Python environment installed on this machine.

If Windows reports `No suitable Python runtime found`, install Python 3.11 or
point the script at an existing Python runtime:

```powershell
$env:DAY_DISTILLER_PYTHON="D:\ESP-IDF\.espressif\python_env\idf6.0_py3.11_env\Scripts\python.exe"
.\RunClient.bat
```

## Test

```powershell
python -m unittest discover -s tests
```

## Build EXE

Double-click `BuildExe.bat`, or run:

```powershell
.\scripts\build.ps1
```

The packaged application is written to:

```text
dist\DayDistillerClient\DayDistillerClient.exe
```

After packaging, the EXE can be launched directly on this Windows machine.

## Device Connection

1. Flash firmware from the `usb-link-main` branch to the device.
2. Insert a TF card. FAT32 is recommended.
3. Connect the device to the PC with a USB data cable.
4. Wait for Windows to enumerate the serial device. In normal mode the firmware
   exposes a log/debug CDC interface and a USB Link protocol CDC interface.
5. Launch the client and click `Auto Find`. If auto find fails, select the COM
   port manually and click `Connect`.

## Basic Workflow

1. Confirm the UI shows `Connected` and `mode=serial`.
2. Choose `Read / Write` or `Read Only`.
3. Click `Enter U Disk`.
4. The device reboots. The serial port disconnects briefly, then Windows should
   show a new removable drive.
5. Use the drive in Windows Explorer to read or copy files from the TF card.
6. When finished, click `Eject + Exit` in the client. This safely ejects the
   drive and sends `EXIT_MSC` so the device returns to serial mode.

Use `Force Exit` only when Windows has already ejected the drive or when the
drive did not mount and there is no file copy in progress.

## Notes

- Do not unplug the device or force exit while Windows is writing files.
- During MSC mode, the USB serial ports briefly disappear and reappear.
- In MSC mode the firmware exposes protocol CDC + mass storage. The normal log
  CDC is restored after returning to serial mode.
- If Windows does not show a drive letter, click `Refresh`, wait a few seconds,
  and try `Auto Find` again. A damaged or unsupported TF filesystem may still
  require formatting or card replacement.

## Protocol

The client implements USB Link protocol version 1:

- SLIP framed binary header plus JSON payload.
- CRC32 over header and payload.
- Command CDC is the second CDC interface in serial mode and the only CDC
  interface in MSC mode.
- `ENTER_MSC` reboots the device into CDC + MSC mode.
- `EXIT_MSC` should be sent only after the Windows volume is safely ejected,
  unless the user explicitly forces exit.
