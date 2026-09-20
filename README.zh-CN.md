# Day Distiller · 早期 Windows 客户端

[English](README.md) · **简体中文**

> **历史开发分支：** 这里保留早期 Windows USB 工具作为开发快照。当前的日记应用位于 [desktop-app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)。

这里提供设备发现、USB 状态、存储模式和文件访问，配套固件位于 [old/usb-link-dev-firmware](https://github.com/zjmdong/Day_Distiller/tree/old/usb-link-dev-firmware)。

## 在 Windows 上运行

```powershell
git clone --branch old/windows-dev-client-py https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m day_distiller_client
```

这版工具使用 Python 和 PySide6；完整项目见 [firmware-production](https://github.com/zjmdong/Day_Distiller/tree/firmware-production)。

## 许可

此分支采用[知识共享 署名—非商业性使用 4.0 国际许可协议](LICENSE.md)（CC BY-NC 4.0）。
