# Day Distiller · Historical USB Link firmware

**English** · [简体中文](README.zh-CN.md)

> [!WARNING]
> **Historical development branch: `old/usb-link-dev-firmware`.** This is an earlier USB integration snapshot, not the recommended firmware for a new build. Start at [`firmware-production`](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) and use the current [`desktop-app`](https://github.com/zjmdong/Day_Distiller/tree/desktop-app).

## Snapshot state

Reviewed **2026-09-20**, code baseline **`319b7cb`** (`Add USB Link firmware maintenance mode`). This snapshot adds optional TinyUSB CDC control and USB mass-storage maintenance to the earlier HW 2.0 capture firmware.

It contains camera/audio/IMU capture, TF storage, Wi-Fi/web configuration and power-management code, but it predates the current dedicated device identity/status/settings, transactional export, recording-metadata and USB-session services. Do not infer modern export/deletion safeguards or 2.1 configuration commands from the presence of a USB port.

## Useful for

- Studying the original CDC-to-MSC transition and early host/firmware integration.
- Reproducing an issue specifically tied to this snapshot.
- Comparing earlier design decisions with the current implementation.

The historical host counterpart is [`old/windows-dev-client-py`](https://github.com/zjmdong/Day_Distiller/tree/old/windows-dev-client-py), not the retired local branch names that may appear in older commits.

## Development setup

Use **ESP-IDF 6.0.1** and inspect this branch's [`day_pins.h`](main/include/day_pins.h), [`sdkconfig.defaults`](sdkconfig.defaults) and [`main/idf_component.yml`](main/idf_component.yml) before building:

```sh
git clone --branch old/usb-link-dev-firmware https://github.com/zjmdong/Day_Distiller.git Day_Distiller_Legacy_Firmware
cd Day_Distiller_Legacy_Firmware
idf.py set-target esp32s3
idf.py build
```

The full ESP-IDF examples tree is required for the captive-portal DNS component. This snapshot does not include the modern firmware branch's host-side contract-test suite. A successful compile does not demonstrate reliability on hardware.

## USB and safety boundaries

- See this snapshot's [protocol document](docs/usb_link_protocol.md) for its own supported command set and interface assignment.
- Use `HELLO` discovery; do not assume current CDC ordering or device capabilities.
- Back up the card and start with read-only access. Eject before returning from MSC; do not unplug during host writes.
- Do not use private recordings or sensitive Wi-Fi credentials for legacy testing. The configuration interface is a prototype, not a hardened authenticated service.
- For compatible hardware context, see the [current hardware interface guide](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/HARDWARE.md), then re-check every relevant assumption against this older branch.

**JerryZ** independently developed Day Distiller. Original manufacturing files remain private; this branch does not add an open-source license. See the [main project](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) for current scope and status.
