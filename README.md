<div align="center">

<img src="assets/day-distiller-icon.png" alt="Day Distiller" width="88" />

# Day Distiller Desktop

### A place for the moments that made the day.

The desktop companion for the Day Distiller wearable.

[English](README.md) · [简体中文](README.zh-CN.md)

[Main project](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) · [Features](#features) · [Quick start](#quick-start) · [Hardware](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/HARDWARE.md)

</div>

---

## Overview

The wearable gathers short glimpses of everyday life. This app gives those fragments a home: connect the device, revisit a day, choose a visual style and turn the selected moments into a journal and illustrated poster.

The goal is a calmer way to remember a day—one that feels like returning to a story instead of sorting through a folder of recordings. The desktop app, wearable electronics, firmware and AI workflow form a project developed end to end by one person.

## Features

| Area | Experience |
| :--- | :--- |
| **Start** | Guided connection, import and daily generation. |
| **Memories** | Journals organised by date, with editing and regeneration. |
| **Appearance & style** | Personal reference images, visual styles and poster previews. |
| **Sharing** | Illustrated poster, PDF and email. |
| **Settings** | Device options, AI services and an offline demo. |

## Technology

Python · PySide6 · SQLite · Pydantic · FFmpeg · NumPy · scikit-learn · pytest · Nuitka

## Quick start

Install Python 3.11 and FFmpeg/FFprobe, then clone the desktop branch.

**Windows (PowerShell)**

```powershell
git clone --branch desktop-app https://github.com/zjmdong/Day_Distiller.git Day_Distiller_Desktop
cd Day_Distiller_Desktop
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m day_distiller_client
```

**Apple Silicon macOS**

```sh
git clone --branch desktop-app https://github.com/zjmdong/Day_Distiller.git Day_Distiller_Desktop
cd Day_Distiller_Desktop
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m day_distiller_client
```

To explore without hardware or cloud services, open **Settings → Developer settings**, select a copy of a compatible recording folder and choose **Offline demo** on the generation page. Keep the virtual-card deletion option off. The [firmware setup guide](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/GETTING_STARTED.md) describes the recording files.

For daily use, connect the device over USB, import a date and create a journal from the Start page. AI and email services can be added in Settings. The interface is primarily in Chinese.

## Development and guides

```sh
python -m pytest -q
```

Build on the target platform with scripts/build.ps1 (Windows) or scripts/build_macos.sh (Apple Silicon macOS).

- [User guide](docs/user-guide.md)
- [AI and email setup](docs/mainland-model-setup.md) · [SMTP](docs/smtp-setup.md)
- [Build guide](docs/nuitka-build.md) · [Troubleshooting](docs/usb-ffmpeg-troubleshooting.md)

## License

This branch is shared under [Creative Commons Attribution-NonCommercial 4.0 International](LICENSE.md) (CC BY-NC 4.0).
