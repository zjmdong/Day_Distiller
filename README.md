<div align="center">

# Day Distiller

### Keep the moments. Distil the day.

A wearable memory companion that turns fragments of an ordinary day into a story worth revisiting.

**Custom hardware · Embedded firmware · Desktop application · Multimodal AI**

[English](README.md) · [简体中文](README.zh-CN.md)

[The project](#the-project) · [Experience](#one-experience-end-to-end) · [Engineering](#engineering-at-a-glance) · [Get started](#get-started) · [Hardware](docs/HARDWARE.md) · [Desktop app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)

</div>

---

## The project

The moments that define a day are rarely the ones we remember to photograph. A camera asks us to stop and capture them; a diary asks us to reconstruct them later. Recording everything leaves a mountain of footage, but not necessarily a memory.

Day Distiller explores another rhythm: **collect a little, understand the day, keep what matters.** A small wearable gathers brief glimpses of video, sound and movement throughout the day. When connected to a computer, its desktop companion brings the fragments together, helps identify meaningful moments and transforms them into a Daily Journal and illustrated poster.

The result is more than a gallery of clips. It is a way to return to the people, places and small discoveries that might otherwise disappear into the routine.

## One experience, end to end

**Wear → Capture → Store → Connect → Verify → Distil → Revisit**

| Part of the experience | Capabilities |
| :--- | :--- |
| **Wearable** | Brief scheduled or intentional recordings; visual, audio and motion context; local storage; device status and settings. |
| **Transfer** | USB connection, day-based browsing and verified import into the desktop library. |
| **Daily Journal** | AI-assisted selection of moments, readable reflections and an illustrated daily poster. |
| **Personal space** | Journal history, editing, visual styles, PDF export and email sharing. |
| **Development path** | An offline desktop demo and documented entry points for compatible hardware and host applications. |

## Engineering at a glance

Day Distiller brings the physical object and the software experience into one working prototype. Its electronics, two PCB revisions, wearable enclosure, embedded firmware, desktop application and journal workflow were developed independently by one person. This branch contains the firmware for the custom HW 2.0 board; the companion app lives on [desktop-app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app).

- **Coherent multimodal records.** Video, audio, motion and timing belong to the same capture, giving later analysis the context to interpret a moment.
- **A deliberate record lifecycle.** Recordings move from acquisition to local storage and USB export as identifiable, complete units.
- **A dependable handoff.** The desktop experience checks imported files, tracks progress and supports recovery when a transfer is interrupted.
- **AI with an editorial role.** The journal workflow connects evidence from individual recordings to a day-level narrative, then pairs the result with a visual poster.
- **Hardware/software co-design.** The custom board, enclosure, device interface and desktop companion are designed around the same daily-use journey.

For deeper interface details, see the [hardware guide](docs/HARDWARE.md), [USB integration guide](docs/USB_INTEGRATION.md) and [desktop application](https://github.com/zjmdong/Day_Distiller/tree/desktop-app).

## Technology stack

| Area | Technologies |
| :--- | :--- |
| Electronics and industrial design | EasyEDA, CAD, SLA 3D printing |
| Firmware | C, ESP-IDF, FreeRTOS, CMake |
| Desktop | Python, PySide6, SQLite, Pydantic |
| Media and AI | FFmpeg, NumPy, scikit-learn, Qwen, DeepSeek, Seedream |
| Testing and distribution | pytest, Nuitka, GitHub Actions |

## Get started

**Explore the desktop experience:** follow the [desktop-app setup](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/README.md#quick-start). Its offline demo can be used with compatible sample recordings.

**Build the firmware:** activate [ESP-IDF 6.0.1 for ESP32-S3](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/index.html), then run:

```sh
git clone --branch firmware-production https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
```

The [getting started guide](docs/GETTING_STARTED.md) covers flashing and first use. Consult the [hardware guide](docs/HARDWARE.md) before building a compatible device.

## Project guides

| Guide | Use it for |
| :--- | :--- |
| [Getting started](docs/GETTING_STARTED.md) · [中文](docs/GETTING_STARTED.zh-CN.md) | Build, flash and first recording |
| [Hardware](docs/HARDWARE.md) · [中文](docs/HARDWARE.zh-CN.md) | Components and GPIO map for compatible builds |
| [USB integration](docs/USB_INTEGRATION.md) | Connecting a host client |
| [Desktop app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app) | Interface, journal workflow and application setup |
| [Branches](docs/BRANCHES.md) | Current and historical development branches |

### Repository map

- [`main/src/`](main/src) contains the firmware services; [`main/web/`](main/web) holds the device interface.
- [`tests/`](tests) contains firmware tests; [`docs/`](docs) contains integration and hardware guides.
- Desktop source, tests and packaging live on the separate [`desktop-app` branch](https://github.com/zjmdong/Day_Distiller/tree/desktop-app).

## Looking ahead

Day Distiller began with a simple question: what if remembering a day felt less like managing files and more like returning to a story? The project is a foundation for a quieter, more personal way to collect and revisit everyday life.

## License

This project is shared under [Creative Commons Attribution-NonCommercial 4.0 International](LICENSE.md) (CC BY-NC 4.0).
