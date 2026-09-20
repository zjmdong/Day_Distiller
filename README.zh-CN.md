# Day Distiller · 早期 USB 固件

[English](README.md) · **简体中文**

> **历史开发分支：** 我保留早期固件与 USB 开发代码供参考。当前项目请从 [firmware-production](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) 开始。

这里包含可穿戴设备的采集固件与早期 USB 维护模式。配套的历史客户端位于 [old/windows-dev-client-py](https://github.com/zjmdong/Day_Distiller/tree/old/windows-dev-client-py)。

## 构建

使用 ESP-IDF 6.0.1：

```sh
git clone --branch old/usb-link-dev-firmware https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
```

参阅[早期 USB 说明](docs/usb_link_protocol.md)或[当前硬件文档](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/HARDWARE.zh-CN.md)。

## 许可

我以 [PolyForm Noncommercial License 1.0.0](LICENSE.md) 发布此分支。
