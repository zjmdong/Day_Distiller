<div align="center">

# Day Distiller

### Keep the moments. Distil the day.

A wearable device and desktop companion for turning everyday fragments into a visual journal.

[English](README.md) · [简体中文](README.zh-CN.md)

[The idea](#the-idea) · [Features](#features) · [Vision](#vision) · [Get started](#get-started) · [Hardware](docs/HARDWARE.md) · [Desktop app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)

</div>

---

## The idea

Some days are full of moments worth keeping, yet surprisingly hard to recall by evening. A phone camera asks for attention in the moment; a diary asks for memory afterwards. Continuous recording creates more material than most people want to revisit.

Day Distiller explores a quieter middle ground. A small wearable captures brief glimpses of video, sound and movement. Later, the desktop app brings those fragments together, helps select meaningful moments and creates a daily journal with an illustrated poster.

## Features

| | What you can do |
| :--- | :--- |
| **Capture** | Keep short visual, audio and motion glimpses at intervals or on demand. |
| **Explore the device** | Preview the camera, check device status and adjust settings. |
| **Bring moments home** | Connect over USB and organise imported recordings by day. |
| **Distil a day** | Use AI to help select moments and compose a daily journal. |
| **Make it yours** | Choose a visual style and reference image for an illustrated daily poster. |
| **Revisit and share** | Browse past journals, edit them, export a PDF or send one by email. |
| **Try it offline** | Explore the desktop workflow with a local demo. |

## Vision

The ambition is a personal memory companion that makes ordinary days easier to revisit. Over time, a collection of small moments can become a quiet archive of daily life: a place to notice what mattered and return to stories worth keeping.

## One project, from hardware to software

The custom electronics, wearable enclosure, embedded firmware, desktop app and AI-assisted journal were designed and built end to end by a single developer. The firmware lives on this branch; the companion app is on [desktop-app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app).

| Area | Technologies |
| :--- | :--- |
| Hardware design | EasyEDA, CAD, SLA 3D printing |
| Firmware | C, ESP-IDF, FreeRTOS, CMake |
| Desktop | Python, PySide6, SQLite, Pydantic |
| Media and AI | FFmpeg, NumPy, scikit-learn, Qwen, DeepSeek, Seedream |
| Build and testing | pytest, Nuitka, GitHub Actions |

## Get started

The firmware targets the custom HW 2.0 board. With [ESP-IDF 6.0.1](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/index.html) activated:

```sh
git clone --branch firmware-production https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
```

The [getting started guide](docs/GETTING_STARTED.md) covers flashing and first use. For the desktop experience, follow the [desktop-app README](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/README.md).

## Documentation

| Guide | Use it for |
| :--- | :--- |
| [Getting started](docs/GETTING_STARTED.md) · [中文](docs/GETTING_STARTED.zh-CN.md) | Build, flash and first recording |
| [Hardware](docs/HARDWARE.md) · [中文](docs/HARDWARE.zh-CN.md) | Components and GPIO map for compatible builds |
| [USB integration](docs/USB_INTEGRATION.md) | Connecting a host client |
| [Branches](docs/BRANCHES.md) | Current and historical development branches |

## License

This project is shared under [Creative Commons Attribution-NonCommercial 4.0 International](LICENSE.md) (CC BY-NC 4.0).
