# 硬件文档 · HW 2.0

[English](HARDWARE.md) · [简体中文](HARDWARE.zh-CN.md) · [项目首页](../README.zh-CN.md)

本文提供兼容硬件搭建与固件移植所需的接口信息。GPIO 已对照 [`main/include/day_pins.h`](../main/include/day_pins.h) 核实；代码变化后，应以驱动及构建配置为准。

> **公开边界：** 不提供原始原理图、PCB、Gerber、BOM 或机械 CAD。仅凭本文无法原样制造原板。搭建兼容版本，需要自行完成电路设计或选择合适模块，并验证供电、接线与固件假设。

## 硬件概况

HW 2.0 是以 ESP32-S3 为核心的定制可穿戴电路板，集成图像、声音、运动、时钟、存储与状态反馈。JerryZ 独立完成两版 PCB 的设计与组装，并制作了透明 SLA 打印磁吸外壳。第二版针对第一版出现的装配与摄像头连接器问题进行了调整。

| 功能 | 器件 / 接口 | 固件对应 |
| :--- | :--- | :--- |
| 主控 | **ESP32-S3-WROOM-1-N16R8** | 16 MB Flash、8 MB Octal PSRAM；目标 `esp32s3` |
| 摄像头 | **OV5640** | 8 位并行数据、独立 SCCB/I²C 控制；`esp32-camera` |
| 运动传感器 | **LSM6DS3TR-C** | 加速度计与陀螺仪，共享传感器 I²C；INT1 唤醒线 |
| 麦克风 | **MSM261S4030H0R** | I²S 输入；L/R 选择脚接地 |
| 实时时钟 | **PCF8563T** | 传感器 I²C；电池域持续供电 |
| 电量计 | **MAX17048G+T10** | 通过传感器 I²C 读取电池状态 |
| 存储 | **microSD / TF 卡** | SPI / FATFS；按请求切换 USB MSC |
| 状态灯 | **3 颗 WS2812B** | 串联 RGB，由灯带服务驱动 |
| 电源 | **TPS63020** 与摄像头 LDO | 板级电源转换；相机 2.8 V / 1.5 V 电源轨 |
| 主机连接 | **USB-C** | ESP32-S3 原生 USB 数据线；CDC 与 MSC |

此表是功能性器件概览，**不是制造 BOM**。无源器件数值、封装、连接器触点编号和保护电路不在公开范围内。

## 供电与总线边界

- RTC 与电量计直接位于常供电的电池域，不受主电源开关控制。关闭主电路不等于拔掉电池。
- ESP32 模组应使用符合规格的稳压电源；不能将电池原始电压或 USB VBUS 接到 GPIO。各传感器及模块的电源、I/O 要求需要分别核对。
- 相机具有独立电源轨。固件定义了 `RESET`，但未定义相机电源使能或 `PWDN` GPIO（`pin_pwdn = -1`）。不能假定深睡会物理切断相机供电。
- 传感器总线与相机控制使用不同引脚；SCCB 由相机驱动配置，不要在相同引脚上重复初始化其他总线。
- 参考配置使用 16 MB Flash、QIO Flash 与 80 MHz Octal PSRAM。替换模组时核对 [`sdkconfig.defaults`](../sdkconfig.defaults)。

| 总线 | GPIO | 器件 / 地址 |
| :--- | :--- | :--- |
| 传感器 I²C / I2C0 | SDA **8**、SCL **9** | MAX17048：`0x36`；PCF8563：`0x51`；IMU 依次探测 `0x6A`、`0x6B` |
| 相机控制 / I2C1 引脚 | SDA/SIOD **6**、SCL/SIOC **7** | OV5640，由 `esp32-camera` 配置 |
| I²S | SCK **40**、WS **41**、SD **38** | 麦克风数据输入 ESP32-S3 |
| microSD SPI | CS **47**、MOSI **1**、MISO **2**、SCK **48** | SPI 模式，不是四线 SDMMC |
| USB | D− **19**、D+ **20** | 原生 USB，不是 USB 转串口桥 |

这里的 I²C 地址为 **7 位地址**，不是左移后的读写字节。IMU 的 `WHO_AM_I` 是身份寄存器值，不是总线地址。对应代码：[`imu.c`](../main/src/drivers/imu.c)、[`rtc_clock.c`](../main/src/drivers/rtc_clock.c)、[`battery.c`](../main/src/drivers/battery.c)。

## GPIO 定义

以下数字是 **ESP32-S3 GPIO 编号**，不是模组焊盘序号、开发板排针序号或相机 FPC 触点编号。

### 系统与外设

| 信号 | GPIO | 固件常量 / 说明 |
| :--- | ---: | :--- |
| USB D− | 19 | `DAY_PIN_USB_D_MINUS` |
| USB D+ | 20 | `DAY_PIN_USB_D_PLUS` |
| 传感器 SDA | 8 | `DAY_PIN_I2C0_SDA` |
| 传感器 SCL | 9 | `DAY_PIN_I2C0_SCL` |
| IMU INT1 | 4 | `DAY_PIN_IMU_INT`；可选运动唤醒 |
| TF CS | 47 | `DAY_PIN_TF_CS` |
| TF MOSI | 1 | `DAY_PIN_TF_MOSI` |
| TF MISO | 2 | `DAY_PIN_TF_MISO` |
| TF SCK | 48 | `DAY_PIN_TF_SCK` |
| 麦克风 SCK | 40 | `DAY_PIN_MIC_SCK` |
| 麦克风 WS | 41 | `DAY_PIN_MIC_WS` |
| 麦克风 SD | 38 | `DAY_PIN_MIC_SD` |
| RGB DIN | 42 | `DAY_PIN_RGB_DIN`；3 颗灯串联 |

### OV5640 摄像头

| 信号 | GPIO | 固件常量 |
| :--- | ---: | :--- |
| RESET | 3 | `DAY_PIN_CAM_RST` |
| SIOD / SDA | 6 | `DAY_PIN_CAM_SIOD` |
| SIOC / SCL | 7 | `DAY_PIN_CAM_SIOC` |
| MCLK / XCLK | 15 | `DAY_PIN_CAM_MCLK` |
| PCLK | 16 | `DAY_PIN_CAM_PCLK` |
| VSYNC | 17 | `DAY_PIN_CAM_VSYNC` |
| HREF | 18 | `DAY_PIN_CAM_HREF` |
| D0 | 10 | `DAY_PIN_CAM_D0` |
| D1 | 11 | `DAY_PIN_CAM_D1` |
| D2 | 12 | `DAY_PIN_CAM_D2` |
| D3 | 13 | `DAY_PIN_CAM_D3` |
| D4 | 14 | `DAY_PIN_CAM_D4` |
| D5 | 21 | `DAY_PIN_CAM_D5` |
| D6 | 39 | `DAY_PIN_CAM_D6` |
| D7 | 45 | `DAY_PIN_CAM_D7` |

当前固件引脚表**没有独立的拍摄按键 GPIO**。实体原型具有电源/采集交互，但本文不推测其未公开的电气连接。兼容版本可先使用网页采集命令和定时采集；增加独立按键需要自行设计接线与处理逻辑。

## 首次调试清单

1. **断电检查。** 检查极性、短路、连接器方向和相机排线触点顺序。不能仅因标注“OV5640”就认为接线兼容。
2. **验证供电与启动。** 使用适当限流的电源，在连接敏感外设前检查各电源轨，再确认启用 Flash/PSRAM 的主控能够启动。
3. **检查传感器 I²C。** 板级服务启动时会探测总线。在排查上层功能前，先确认器件地址与驱动错误。
4. **逐项验证数据链路。** 检查网页相机预览、麦克风波形与 IMU 遥测；麦克风 L/R 接地这一前提应保留，否则需调整采样逻辑。
5. **检查存储。** 使用已备份、FAT32 格式的测试卡。挂载失败时固件不会自动格式化存储卡。
6. **完成一次完整记录。** 检查 `video.avi`、`audio.wav`、`imu.json`、`meta.json` 及实际采集速率、错误状态，再验证桌面导入和安全弹出。
7. **反复测试。** 使用可丢弃数据验证唤醒/休眠、低电量与 USB 中断；软件测试不能替代实板测量。

## 移植注意事项

- 从 [`day_pins.h`](../main/include/day_pins.h) 调整引脚，再检查 [`board.c`](../main/src/board/board.c) 及相关驱动；仅改引脚不保证新传感器兼容。
- GPIO3 与 GPIO45 具有启动绑带功能。应根据准确的模组型号，对照 [ESP32-S3-WROOM-1 数据手册](https://www.espressif.com/sites/default/files/documentation/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf) 核对复位电平、PSRAM 占用引脚和电气限制。
- 替代相机模块可能使用不同稳压、复位接法和 FPC 顺序，应以模块资料为准，不直接套用连接器布局。
- 不要让主机和固件同时访问文件系统，使用 [USB 集成指南](USB_INTEGRATION.md) 中的维护模式和弹出流程。
- 续航、充电安全、机械装配与温升必须在自己的实现上验证；本文不提供实测续航或完整充电设计。

下一步：[构建、烧录与首次使用](GETTING_STARTED.zh-CN.md)。
