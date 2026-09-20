# Getting started

[English](GETTING_STARTED.md) · [简体中文](GETTING_STARTED.zh-CN.md) · [Project](../README.md)

Use this guide to build the firmware and make your first recording on compatible hardware.

## What you need

- The custom HW 2.0 board or a compatible build based on the [hardware guide](HARDWARE.md)
- A FAT32 microSD card and a USB data cable
- [ESP-IDF 6.0.1](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/index.html), including the full examples directory

## Build and flash

In an activated ESP-IDF terminal:

```sh
git clone --branch firmware-production https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
idf.py -p PORT flash
```

Replace PORT with your board's flashing port. The pin map and board components are in [Hardware](HARDWARE.md).

## First recording

1. Insert the card and power on the device.
2. Join the AI_CAM_XXXX Wi-Fi hotspot and open its configuration page.
3. Set the time zone and Wi-Fi details if you wish.
4. Check the preview and start a recording, or let the cold-start capture run.
5. Connect the [desktop app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app) to import the recordings.

The web settings page displays whether a Wi-Fi password has been saved without returning the saved password. A connected hotspot client can keep the configuration page open; after the last client leaves, the device continues to capture.

Each recording folder contains video.avi, audio.wav, imu.json and meta.json. Use a copy of a recording folder to try the desktop app's offline demo.

## Development

Run the firmware tests without a device:

```sh
python -m unittest discover -s tests -p "test_*.py"
```

The hardware_* scripts are optional tools for testing a connected device. For USB client development, see [USB integration](USB_INTEGRATION.md). For the desktop setup, follow its [README](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/README.md).
