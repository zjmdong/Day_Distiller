# 硬件文档 · HW 2.0

[English](HARDWARE.md) · [简体中文](HARDWARE.zh-CN.md) · [项目首页](../README.zh-CN.md)

我为 Day Distiller 设计并组装了可穿戴电路板与外壳。这里列出主要器件和固件引脚定义，方便搭建兼容硬件。

## 主要器件

| 用途 | 器件 |
| :--- | :--- |
| 主控 | ESP32-S3-WROOM-1-N16R8 |
| 摄像头 | OV5640 |
| 运动传感器 | LSM6DS3TR-C |
| 麦克风 | MSM261S4030H0R |
| 实时时钟 | PCF8563T |
| 电量计 | MAX17048G+T10 |
| 电源转换 | TPS63020 |
| 存储 | microSD |
| 指示灯 | 三颗 WS2812B |
| 连接 | USB-C |

主电源关闭时，时钟和电量计仍连接电池。麦克风 L/R 选择脚接地。传感器总线地址：电量计 0x36、时钟 0x51、IMU 0x6A 或 0x6B。

## GPIO 引脚

下表使用 ESP32-S3 的 GPIO 编号。对应定义见 [day_pins.h](../main/include/day_pins.h)。

| 信号 | GPIO | 固件定义 |
| :--- | ---: | :--- |
| USB D− | 19 | `DAY_PIN_USB_D_MINUS` |
| USB D+ | 20 | `DAY_PIN_USB_D_PLUS` |
| 传感器 SDA | 8 | `DAY_PIN_I2C0_SDA` |
| 传感器 SCL | 9 | `DAY_PIN_I2C0_SCL` |
| IMU INT1 | 4 | `DAY_PIN_IMU_INT` |
| TF CS | 47 | `DAY_PIN_TF_CS` |
| TF MOSI | 1 | `DAY_PIN_TF_MOSI` |
| TF MISO | 2 | `DAY_PIN_TF_MISO` |
| TF SCK | 48 | `DAY_PIN_TF_SCK` |
| 麦克风 SCK | 40 | `DAY_PIN_MIC_SCK` |
| 麦克风 WS | 41 | `DAY_PIN_MIC_WS` |
| 麦克风 SD | 38 | `DAY_PIN_MIC_SD` |
| RGB DIN | 42 | `DAY_PIN_RGB_DIN` |
| 相机 RESET | 3 | `DAY_PIN_CAM_RST` |
| 相机 SIOD | 6 | `DAY_PIN_CAM_SIOD` |
| 相机 SIOC | 7 | `DAY_PIN_CAM_SIOC` |
| 相机 MCLK | 15 | `DAY_PIN_CAM_MCLK` |
| 相机 PCLK | 16 | `DAY_PIN_CAM_PCLK` |
| 相机 VSYNC | 17 | `DAY_PIN_CAM_VSYNC` |
| 相机 HREF | 18 | `DAY_PIN_CAM_HREF` |
| 相机 D0 | 10 | `DAY_PIN_CAM_D0` |
| 相机 D1 | 11 | `DAY_PIN_CAM_D1` |
| 相机 D2 | 12 | `DAY_PIN_CAM_D2` |
| 相机 D3 | 13 | `DAY_PIN_CAM_D3` |
| 相机 D4 | 14 | `DAY_PIN_CAM_D4` |
| 相机 D5 | 21 | `DAY_PIN_CAM_D5` |
| 相机 D6 | 39 | `DAY_PIN_CAM_D6` |
| 相机 D7 | 45 | `DAY_PIN_CAM_D7` |

固件目前没有单独定义拍摄按键 GPIO。原始原理图、PCB、BOM 和外壳 CAD 保留在本地；本文提供设计兼容电路所需的接口信息。

## 移植

1. 对照引脚表检查器件连线。
2. 更换器件时，修改 [day_pins.h](../main/include/day_pins.h) 和对应驱动。
3. 查阅 [ESP32-S3 模组数据手册](https://www.espressif.com/sites/default/files/documentation/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf)，特别留意 GPIO3 与 GPIO45。
4. 按[上手指南](GETTING_STARTED.zh-CN.md)构建、烧录并测试。
