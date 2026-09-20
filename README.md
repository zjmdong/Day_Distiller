# Day Distiller · Firmware development

**English** · [简体中文](README.zh-CN.md)

> **Development branch:** I use firmware-dev to work on the wearable firmware. For a new build, start with [firmware-production](https://github.com/zjmdong/Day_Distiller/tree/firmware-production).

This branch contains the current capture, device settings, USB transfer and power management code. It may include work being prepared for the main branch.

## Build and test

Use ESP-IDF 6.0.1:

```sh
git clone --branch firmware-dev https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
python -m unittest discover -s tests -p "test_*.py"
```

I keep the [hardware guide](docs/HARDWARE.md), [setup guide](docs/GETTING_STARTED.md) and [branch guide](docs/BRANCHES.md) here for development. The desktop app is on [desktop-app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app).

## License

I share this branch under the [PolyForm Noncommercial License 1.0.0](LICENSE.md).
