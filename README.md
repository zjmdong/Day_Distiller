<div align="center">

# Day Distiller

### Keep the moments. Distil the day.

A wearable device and desktop app for turning everyday moments into a visual journal.

**English** · [简体中文](README.zh-CN.md)

[Get started](docs/GETTING_STARTED.md) · [Hardware](docs/HARDWARE.md) · [Desktop app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)

</div>

---

## About

I built Day Distiller as a personal project to make it easier to revisit the small moments of a day. I designed the wearable hardware, wrote its firmware and desktop app, and brought the whole experience together myself.

The device captures short video, audio and motion clips. The companion app imports them and helps create a daily journal and illustrated poster.

## Features

- Short recordings with scheduled and manual capture
- Camera preview, device settings and status
- USB transfer and an organised recording library
- AI-assisted moment selection and daily journal creation
- Illustrated poster, PDF export and email sharing
- Offline demo mode for exploring the desktop app

## Technology

| Area | Tools and technologies |
| :--- | :--- |
| Hardware design | EasyEDA, CAD, SLA 3D printing |
| Firmware | C, ESP-IDF, FreeRTOS, CMake |
| Desktop | Python, PySide6, SQLite, Pydantic |
| Media and AI | FFmpeg, NumPy, scikit-learn, Qwen, DeepSeek, Seedream |
| Build and testing | pytest, Nuitka, GitHub Actions |

## Get started

The firmware on this branch targets my custom HW 2.0 board. Use an activated [ESP-IDF 6.0.1](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/index.html) environment:

```sh
git clone --branch firmware-production https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
```

For flashing and first use, follow the [setup guide](docs/GETTING_STARTED.md). The [hardware guide](docs/HARDWARE.md) lists the components and pin map for compatible builds.

The desktop app lives on the separate [desktop-app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app) branch. Its README covers installation, an offline demo and development.

## Documentation

- [Getting started](docs/GETTING_STARTED.md) · [中文](docs/GETTING_STARTED.zh-CN.md)
- [Hardware and GPIO](docs/HARDWARE.md) · [中文](docs/HARDWARE.zh-CN.md)
- [USB integration](docs/USB_INTEGRATION.md)
- [Branches](docs/BRANCHES.md)

## License

I share this project under the [PolyForm Noncommercial License 1.0.0](LICENSE.md). Noncommercial use, modification and distribution are permitted under its terms.
