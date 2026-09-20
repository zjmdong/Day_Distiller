# Day Distiller · Earlier USB firmware

**English** · [简体中文](README.zh-CN.md)

> **Historical development branch:** This snapshot preserves earlier firmware and USB development for reference. Use [firmware-production](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) for the current project.

This branch includes the wearable capture firmware and an early USB maintenance mode. Its companion is [old/windows-dev-client-py](https://github.com/zjmdong/Day_Distiller/tree/old/windows-dev-client-py).

## Build

Use ESP-IDF 6.0.1:

```sh
git clone --branch old/usb-link-dev-firmware https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
```

See the [historical USB reference](docs/usb_link_protocol.md) or the [current hardware guide](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/HARDWARE.md).

## License

This branch is shared under [Creative Commons Attribution-NonCommercial 4.0 International](LICENSE.md) (CC BY-NC 4.0).
