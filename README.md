<div align="center">

<img src="assets/day-distiller-icon.png" alt="Day Distiller" width="88" />

# Day Distiller Desktop

### A place for the moments that made the day.

From captured fragments to a day worth revisiting.

**Device connection · Verified import · Daily Journal · Illustrated memories**

[English](README.md) · [简体中文](README.zh-CN.md)

[Main project](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) · [The experience](#the-experience) · [Features](#features) · [Quick start](#quick-start) · [Hardware](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/HARDWARE.md)

</div>

---

## Overview

The moments that make a day memorable are often too small to notice while they happen. The Day Distiller wearable collects short glimpses of everyday life; this desktop companion gives those fragments a home.

Connect the device, choose a day and bring its recordings into a local library. From there, the application helps turn visual, audio and motion context into a readable journal and an illustrated poster. The goal is a calmer way to remember—more like returning to a story than sorting through a folder of clips.

The desktop app, custom wearable electronics, firmware and AI workflow were developed end to end by one person. See the [main project](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) for the complete system.

## The experience

**Connect → Verify → Explore → Distil → Revisit**

- **A guided connection.** Find the wearable, select a date and follow an import with visible progress.
- **A trusted local library.** Imported files are checked before they become part of the day's collection; interrupted work can be retried.
- **A journal with context.** AI-assisted selection brings together evidence from the recordings into a day-level reflection.
- **A personal result.** Visual styles and reference images shape the poster; past journals stay available to browse and edit.

## Features

| Area | Experience |
| :--- | :--- |
| **Start** | Guided connection, day-based import and daily generation. |
| **Memories** | Journals organised by date, with editing and regeneration. |
| **Appearance & style** | Personal reference images, visual styles and poster previews. |
| **Sharing** | Illustrated poster, PDF export and email. |
| **Settings** | Device options, AI services and an offline demo. |

## Technology stack

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

## For developers

| Area | Starting point |
| :--- | :--- |
| Desktop application | [Application source](src/day_distiller_client) |
| Hardware and firmware | [Main project](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) · [hardware interface](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/HARDWARE.md) |
| Host/device connection | [USB integration](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/USB_INTEGRATION.md) |
| Application workflows | [User guide](docs/user-guide.md) · [AI and email setup](docs/mainland-model-setup.md) |

## Build and guides

```sh
python -m pytest -q
```

Build on the target platform with scripts/build.ps1 (Windows) or scripts/build_macos.sh (Apple Silicon macOS).

- [User guide](docs/user-guide.md)
- [AI and email setup](docs/mainland-model-setup.md) · [SMTP](docs/smtp-setup.md)
- [Build guide](docs/nuitka-build.md) · [Troubleshooting](docs/usb-ffmpeg-troubleshooting.md)

## License

This branch is shared under [Creative Commons Attribution-NonCommercial 4.0 International](LICENSE.md) (CC BY-NC 4.0).
