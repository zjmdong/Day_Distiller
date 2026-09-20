<div align="center">

<img src="assets/day-distiller-icon.png" alt="Day Distiller" width="88" />

# Day Distiller Desktop

### Turn daily fragments into a journal.

My desktop companion for the Day Distiller wearable.

**English** · [简体中文](README.zh-CN.md)

[Main project](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) · [Quick start](#quick-start) · [Hardware](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/HARDWARE.md)

</div>

---

## About

I built this app to bring the recordings from my wearable into one place and turn them into memories I can revisit. I developed the desktop interface, device connection, media tools and AI-assisted journal workflow alongside the hardware and firmware.

## Features

- Import and organise recordings by day
- Browse, edit and regenerate journals
- Create an illustrated poster and PDF
- Configure visual styles and AI services
- Share a journal by email
- Explore the workflow in offline demo mode

## Technology

Python · PySide6 · SQLite · Pydantic · FFmpeg · NumPy · scikit-learn · pytest · Nuitka

## Quick start

Install Python 3.11 and FFmpeg/FFprobe. Clone this branch separately from the firmware.

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

For an offline demo, open **Settings → Developer settings**, choose a copy of a compatible recording folder and select **Offline demo** on the generation page. Keep the virtual-card deletion option off. See the [firmware setup guide](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/GETTING_STARTED.md) for the recording files.

For AI and email, add your own service settings in the app. The interface is primarily in Chinese.

## Development

```sh
python -m pytest -q
```

Build on the target platform with scripts/build.ps1 (Windows) or scripts/build_macos.sh (Apple Silicon macOS). More help is in the [user guide](docs/user-guide.md), [build guide](docs/nuitka-build.md) and [troubleshooting guide](docs/usb-ffmpeg-troubleshooting.md).

## License

I share this branch under the [PolyForm Noncommercial License 1.0.0](LICENSE.md).
