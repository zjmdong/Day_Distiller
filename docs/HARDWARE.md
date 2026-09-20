# Hardware · HW 2.0

[English](HARDWARE.md) · [简体中文](HARDWARE.zh-CN.md) · [Project](../README.md)

An interface reference for bringing up compatible hardware and adapting the firmware. GPIO assignments are checked against [`main/include/day_pins.h`](../main/include/day_pins.h); drivers and build configuration are the authority if the code changes.

> **Publication boundary:** original schematics, PCB layouts, Gerbers, BOMs and mechanical CAD are not distributed. This guide is not sufficient to manufacture the original board unchanged. A compatible build requires your own electrical design or suitable modules, followed by validation of power, wiring and firmware assumptions.

## Overview

HW 2.0 is a custom ESP32-S3-based wearable board integrating imaging, sound, motion, timekeeping, storage and status feedback. JerryZ designed and assembled two board revisions and developed a transparent SLA-printed enclosure with a magnetic attachment. The second revision addressed assembly and camera-connector problems found during the first build.

| Function | Component / interface | Firmware integration |
| :--- | :--- | :--- |
| MCU | **ESP32-S3-WROOM-1-N16R8** | 16 MB flash; 8 MB Octal PSRAM; `esp32s3` target |
| Camera | **OV5640** | 8-bit parallel image data, separate SCCB/I²C control; `esp32-camera` |
| Motion | **LSM6DS3TR-C** | Accelerometer + gyroscope on sensor I²C; INT1 wake line |
| Microphone | **MSM261S4030H0R** | I²S input; L/R selection tied to ground |
| Clock | **PCF8563T** | Battery-backed timekeeping on sensor I²C |
| Fuel gauge | **MAX17048G+T10** | Battery state on sensor I²C |
| Storage | **microSD / TF card** | SPI / FATFS, exposed over USB MSC when requested |
| Status | **3 × WS2812B** | Chained RGB LEDs, driven by the LED-strip service |
| Power | **TPS63020** and camera LDO rails | Board-level power conversion; camera 2.8 V / 1.5 V rails |
| Host connection | **USB-C** | ESP32-S3 native USB data pair; CDC control and MSC |

This is a functional component overview, **not a manufacturing BOM**. Passive values, footprints, connector contact numbering and protection circuitry are intentionally outside its scope.

## Power and bus boundaries

- The RTC and fuel gauge are on the always-on battery domain, outside the main power switch. Switching off the main circuit is not equivalent to removing the battery.
- The ESP32 module uses its specified regulated supply; never apply raw battery or USB VBUS to an ESP GPIO. Verify each sensor/module's supply and I/O requirements separately.
- The camera has dedicated rails. The firmware defines `RESET` but no camera power-enable or `PWDN` GPIO (`pin_pwdn = -1`). Do not assume deep sleep physically disconnects camera power.
- The sensor bus and camera-control bus use different pins. The camera driver owns SCCB configuration; do not initialise a competing bus on those pins.
- The reference build uses 16 MB flash, QIO flash mode and 80 MHz Octal PSRAM. Check [`sdkconfig.defaults`](../sdkconfig.defaults) when substituting an ESP32-S3 module.

| Bus | Pins | Devices / address expectations |
| :--- | :--- | :--- |
| Sensor I²C / I2C0 | SDA **8**, SCL **9** | MAX17048: `0x36`; PCF8563: `0x51`; IMU probes `0x6A`, then `0x6B` |
| Camera control / I2C1 pins | SDA/SIOD **6**, SCL/SIOC **7** | OV5640; configured by `esp32-camera` |
| I²S | SCK **40**, WS **41**, SD **38** | Microphone data flows into the ESP32-S3 |
| microSD SPI | CS **47**, MOSI **1**, MISO **2**, SCK **48** | SPI mode, not four-bit SDMMC |
| USB | D− **19**, D+ **20** | Native USB, not a USB-to-UART bridge |

I²C addresses above are **7-bit addresses**, not shifted read/write bytes. The IMU's `WHO_AM_I` value is a device-identification register, not its bus address. See [`imu.c`](../main/src/drivers/imu.c), [`rtc_clock.c`](../main/src/drivers/rtc_clock.c) and [`battery.c`](../main/src/drivers/battery.c).

## GPIO reference

Numbers below are **ESP32-S3 GPIO numbers**, not module pad numbers, development-board header positions or camera FPC contacts.

### System and peripherals

| Signal | GPIO | Firmware constant / note |
| :--- | ---: | :--- |
| USB D− | 19 | `DAY_PIN_USB_D_MINUS` |
| USB D+ | 20 | `DAY_PIN_USB_D_PLUS` |
| Sensor SDA | 8 | `DAY_PIN_I2C0_SDA` |
| Sensor SCL | 9 | `DAY_PIN_I2C0_SCL` |
| IMU INT1 | 4 | `DAY_PIN_IMU_INT`; optional motion wake |
| TF CS | 47 | `DAY_PIN_TF_CS` |
| TF MOSI | 1 | `DAY_PIN_TF_MOSI` |
| TF MISO | 2 | `DAY_PIN_TF_MISO` |
| TF SCK | 48 | `DAY_PIN_TF_SCK` |
| Microphone SCK | 40 | `DAY_PIN_MIC_SCK` |
| Microphone WS | 41 | `DAY_PIN_MIC_WS` |
| Microphone SD | 38 | `DAY_PIN_MIC_SD` |
| RGB DIN | 42 | `DAY_PIN_RGB_DIN`; three LEDs in series |

### OV5640 camera

| Signal | GPIO | Firmware constant |
| :--- | ---: | :--- |
| RESET | 3 | `DAY_PIN_CAM_RST` |
| SIOD / SDA | 6 | `DAY_PIN_CAM_SIOD` |
| SIOC / SCL | 7 | `DAY_PIN_CAM_SIOC` |
| MCLK / XCLK | 15 | `DAY_PIN_CAM_MCLK` |
| PCLK | 16 | `DAY_PIN_CAM_PCLK` |
| VSYNC | 17 | `DAY_PIN_CAM_VSYNC` |
| HREF | 18 | `DAY_PIN_CAM_HREF` |
| D0 | 10 | `DAY_PIN_CAM_D0` |
| D1 | 11 | `DAY_PIN_CAM_D1` |
| D2 | 12 | `DAY_PIN_CAM_D2` |
| D3 | 13 | `DAY_PIN_CAM_D3` |
| D4 | 14 | `DAY_PIN_CAM_D4` |
| D5 | 21 | `DAY_PIN_CAM_D5` |
| D6 | 39 | `DAY_PIN_CAM_D6` |
| D7 | 45 | `DAY_PIN_CAM_D7` |

There is **no independent capture-button GPIO defined** in this firmware's pin map. The physical prototype includes a power/capture interaction, but this document does not assign an undocumented electrical connection to it. For a compatible build, start with the web capture command and timer; adding a dedicated button requires your own wiring and firmware handler.

## Bring-up checklist

1. **Inspect unpowered hardware.** Check polarity, shorts, connector orientation and the exact camera cable contact order. Do not infer pin compatibility from the name “OV5640” alone.
2. **Verify power and boot.** Use an appropriately current-limited supply. Confirm rails before connecting sensitive peripherals, then confirm the ESP32-S3 boots with flash and PSRAM enabled.
3. **Check sensor I²C.** The board service probes the sensor bus during startup. Confirm the expected devices and inspect driver errors before debugging higher-level features.
4. **Check each data path.** Use the web camera preview, microphone waveform and IMU telemetry. The microphone's L/R pin is grounded; preserve that assumption or adapt audio acquisition.
5. **Check storage.** Use a backed-up, FAT32-formatted test card. The firmware deliberately does not format a card when mounting fails.
6. **Make one complete recording.** Inspect `video.avi`, `audio.wav`, `imu.json` and `meta.json`, including actual rates and errors. Then test desktop import and safe eject.
7. **Validate repeated cycles.** Exercise wake/sleep, low-battery behaviour and USB interruptions with disposable data. Software tests are not a substitute for measuring your board.

## Porting notes

- Change pin assignments in [`day_pins.h`](../main/include/day_pins.h), then review [`board.c`](../main/src/board/board.c) and the relevant driver; a header edit alone may not make a different sensor compatible.
- GPIO3 and GPIO45 have strapping functions. Check reset-time behaviour, PSRAM-reserved pins and electrical limits against the [ESP32-S3-WROOM-1 datasheet](https://www.espressif.com/sites/default/files/documentation/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf) for your exact module.
- A replacement camera module may have different regulators, reset wiring or FPC pin order. Validate its documentation rather than copying a connector layout.
- Do not allow host and firmware filesystem access at the same time. Use the USB maintenance transition and eject workflow described in [USB integration](USB_INTEGRATION.md).
- Battery life, charging safety, mechanical fit and thermal behaviour must be validated for your own build; no measured endurance or complete charging design is supplied here.

Continue with [building, flashing and first use](GETTING_STARTED.md).
