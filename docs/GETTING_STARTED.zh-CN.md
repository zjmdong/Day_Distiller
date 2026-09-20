# 上手指南

[English](GETTING_STARTED.md) · [简体中文](GETTING_STARTED.zh-CN.md) · [项目首页](../README.zh-CN.md)

在兼容硬件上构建固件、完成第一次采集，可以按下面的步骤操作。

## 准备

- HW 2.0 定制板，或根据[硬件文档](HARDWARE.zh-CN.md)搭建的兼容设备
- FAT32 格式的 microSD 卡和 USB 数据线
- 包含完整 examples 目录的 [ESP-IDF 6.0.1](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/index.html)

## 构建与烧录

在已激活的 ESP-IDF 终端运行：

```sh
git clone --branch firmware-production https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
idf.py -p PORT flash
```

将 PORT 换成实际烧录端口。器件和接线见[硬件文档](HARDWARE.zh-CN.md)。

## 第一次采集

1. 插入存储卡，开启设备。
2. 连接 AI_CAM_XXXX Wi-Fi 热点，进入配置页面。
3. 按需要设置时区和 Wi-Fi。
4. 查看预览并手动采集，或等待冷启动后的自动采集。
5. 使用[桌面应用](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)导入记录。

网页仅显示 Wi-Fi 密码是否已保存，不会回传已保存的密码。有客户端连接热点时可以持续使用配置页面；最后一个客户端断开后，设备会继续采集流程。

每条记录包含 video.avi、audio.wav、imu.json 和 meta.json。可以复制一份记录，用于桌面端的离线演示。

## 二次开发

无需连接设备即可运行固件测试：

```sh
python -m unittest discover -s tests -p "test_*.py"
```

hardware_* 脚本用于选择性实机测试。开发 USB 客户端时参阅 [USB 集成](USB_INTEGRATION.md)；桌面端安装见其 [README](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/README.zh-CN.md)。
