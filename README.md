# Day Distiller · Earlier Windows client

**English** · [简体中文](README.zh-CN.md)

> **Historical development branch:** This early Windows USB utility remains available as a development snapshot. The current journal app is on [desktop-app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app).

This client offers device discovery, USB status, storage mode and file access. It pairs with [old/usb-link-dev-firmware](https://github.com/zjmdong/Day_Distiller/tree/old/usb-link-dev-firmware).

## Run on Windows

```powershell
git clone --branch old/windows-dev-client-py https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m day_distiller_client
```

This snapshot uses Python and PySide6. See [firmware-production](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) for the full project.

## License

This branch is shared under [Creative Commons Attribution-NonCommercial 4.0 International](LICENSE.md) (CC BY-NC 4.0).
