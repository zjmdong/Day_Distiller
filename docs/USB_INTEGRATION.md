# USB integration

[Project](../README.md) · [Getting started](GETTING_STARTED.md) · [Desktop app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)

USB connects the wearable and desktop app. The link supports device discovery, status and settings, recording transfer, and safe storage hand-off.

## Connecting

The desktop app discovers the device and selects supported features automatically. In normal mode, the device exposes a protocol port and a log port. When transferring files, it switches to a storage mode; reconnect after the switch and eject the volume when finished.

| Mode | USB interfaces |
| :--- | :--- |
| Normal | Protocol CDC and log CDC |
| File transfer | Protocol CDC and mass storage |

## Developing a client

The [desktop client](https://github.com/zjmdong/Day_Distiller/tree/desktop-app) is the reference implementation. For protocol definitions, see [usb_protocol.h](../main/include/usb_protocol.h) and [usb_link.c](../main/src/usb/usb_link.c). A short [USB Link overview](usb_link_protocol.md) is also available.

For a new command, update both the firmware and desktop client and run their tests. Keep file import and deletion as separate user actions.
