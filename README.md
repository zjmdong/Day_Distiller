# Day Distiller · Firmware development

**English** · [简体中文](README.zh-CN.md)

> [!WARNING]
> **Development branch: `firmware-dev`.** Use [`firmware-production`](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) for the main project, public hardware guide and recommended starting point. Do not assume a development build is validated for everyday recording.

[Project overview](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/README.md) · [Hardware](docs/HARDWARE.md) · [Desktop companion](https://github.com/zjmdong/Day_Distiller/tree/desktop-app) · [All branches](docs/BRANCHES.md)

## Current state

Reviewed **2026-09-20**. Before the publication-documentation update, this branch and `firmware-production` shared code commit **`87a15cc`**, whose firmware reports **2.1.0**. The committed runtime code is the same at this checkpoint; the README intentionally identifies this branch as development.

Uncommitted fixes or version notes in a local working directory are **not a released branch state**. Check the current commit and the device's `HELLO` response before reporting compatibility. This branch may diverge after the review date.

## Purpose

- Integrate changes to capture, sensor drivers, power management and the configuration interface.
- Develop USB sessions, settings/status capabilities and storage/export behaviour before promoting them to the main firmware branch.
- Keep reproducible contract tests alongside firmware changes; keep personal logs, credentials and original hardware design files local.

## Build and test

Use an activated **ESP-IDF 6.0.1** environment and compatible **HW 2.0 / ESP32-S3-N16R8** hardware:

```sh
git clone --branch firmware-dev https://github.com/zjmdong/Day_Distiller.git Day_Distiller_Dev
cd Day_Distiller_Dev
idf.py set-target esp32s3
idf.py build
python -m unittest discover -s tests -p "test_*.py" -v
```

See [setup](docs/GETTING_STARTED.md) for baseline hardware, flashing and first-capture details. Run `hardware_*.py` diagnostics only after reviewing their operations, with backed-up or disposable data; they are not ordinary host-only tests.

## Before promoting changes

- Build for the actual ESP32-S3 target and run the host-side contract tests.
- Exercise recording, sleep/wake, low-battery behaviour and USB re-enumeration on hardware; record what was actually tested.
- Preserve the version-1 wire format and published recording schemas, or coordinate an explicit compatibility change with the desktop branch.
- Verify interrupted import/export paths and explicit deletion boundaries using test media.
- Redact logs; do not commit network settings, personal recordings, project source-design materials or generated build outputs.

The current 2.1.0 web configuration has an open-hotspot/plaintext-credential limitation; read [configuration cautions](docs/GETTING_STARTED.md#configuration-and-privacy) before testing with Wi-Fi credentials.

## Author and scope

Independently developed by **JerryZ**. Original hardware manufacturing files are not distributed, and no project-wide software license has been selected. See the [main README](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/README.md#author--licensing) for scope and licensing.
