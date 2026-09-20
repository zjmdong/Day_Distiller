# USB Link

USB Link connects the wearable and desktop app for device discovery, settings, recording transfer and storage access.

The device offers a protocol port and a log port during normal use. File transfer switches to a storage mode; the desktop app reconnects and handles the hand-off. For a new client, start with the [desktop implementation](https://github.com/zjmdong/Day_Distiller/tree/desktop-app) and the [integration guide](USB_INTEGRATION.md).

Protocol definitions are in [usb_protocol.h](../main/include/usb_protocol.h). Test changes to the firmware and desktop client together.
