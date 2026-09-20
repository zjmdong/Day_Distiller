# Hardware · HW 2.0

[English](HARDWARE.md) · [简体中文](HARDWARE.zh-CN.md) · [Project](../README.md)

I designed and assembled this wearable board and its enclosure for Day Distiller. This page lists the main parts and firmware pin assignments for anyone building compatible hardware.

## Main components

| Part | Component |
| :--- | :--- |
| Controller | ESP32-S3-WROOM-1-N16R8 |
| Camera | OV5640 |
| Motion sensor | LSM6DS3TR-C |
| Microphone | MSM261S4030H0R |
| Real-time clock | PCF8563T |
| Battery gauge | MAX17048G+T10 |
| Power conversion | TPS63020 |
| Storage | microSD |
| Indicators | Three WS2812B LEDs |
| Connection | USB-C |

The RTC and battery gauge remain connected to the battery when the main switch is off. The microphone's L/R selection is tied to ground. The sensor bus uses addresses 0x36 (battery gauge), 0x51 (RTC) and 0x6A or 0x6B (IMU).

## GPIO map

These are ESP32-S3 GPIO numbers. The corresponding definitions are in [day_pins.h](../main/include/day_pins.h).

| Signal | GPIO | Firmware definition |
| :--- | ---: | :--- |
| USB D− | 19 | `DAY_PIN_USB_D_MINUS` |
| USB D+ | 20 | `DAY_PIN_USB_D_PLUS` |
| Sensor SDA | 8 | `DAY_PIN_I2C0_SDA` |
| Sensor SCL | 9 | `DAY_PIN_I2C0_SCL` |
| IMU INT1 | 4 | `DAY_PIN_IMU_INT` |
| microSD CS | 47 | `DAY_PIN_TF_CS` |
| microSD MOSI | 1 | `DAY_PIN_TF_MOSI` |
| microSD MISO | 2 | `DAY_PIN_TF_MISO` |
| microSD SCK | 48 | `DAY_PIN_TF_SCK` |
| Mic SCK | 40 | `DAY_PIN_MIC_SCK` |
| Mic WS | 41 | `DAY_PIN_MIC_WS` |
| Mic SD | 38 | `DAY_PIN_MIC_SD` |
| RGB DIN | 42 | `DAY_PIN_RGB_DIN` |
| Camera RESET | 3 | `DAY_PIN_CAM_RST` |
| Camera SIOD | 6 | `DAY_PIN_CAM_SIOD` |
| Camera SIOC | 7 | `DAY_PIN_CAM_SIOC` |
| Camera MCLK | 15 | `DAY_PIN_CAM_MCLK` |
| Camera PCLK | 16 | `DAY_PIN_CAM_PCLK` |
| Camera VSYNC | 17 | `DAY_PIN_CAM_VSYNC` |
| Camera HREF | 18 | `DAY_PIN_CAM_HREF` |
| Camera D0 | 10 | `DAY_PIN_CAM_D0` |
| Camera D1 | 11 | `DAY_PIN_CAM_D1` |
| Camera D2 | 12 | `DAY_PIN_CAM_D2` |
| Camera D3 | 13 | `DAY_PIN_CAM_D3` |
| Camera D4 | 14 | `DAY_PIN_CAM_D4` |
| Camera D5 | 21 | `DAY_PIN_CAM_D5` |
| Camera D6 | 39 | `DAY_PIN_CAM_D6` |
| Camera D7 | 45 | `DAY_PIN_CAM_D7` |

No separate capture-button GPIO is defined in the firmware. My original schematics, PCB files, BOM and enclosure CAD are private; this page provides the information needed to design a compatible board.

## Porting

1. Compare your component wiring with the GPIO map.
2. Update [day_pins.h](../main/include/day_pins.h) and the relevant drivers for any different parts.
3. Check the [ESP32-S3 module datasheet](https://www.espressif.com/sites/default/files/documentation/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf), especially GPIO3 and GPIO45.
4. Follow the [getting started guide](GETTING_STARTED.md) to build, flash and test the firmware.
