# Day Distiller · 早期 USB Link 固件

[English](README.md) · **简体中文**

> [!WARNING]
> **历史开发分支：`old/usb-link-dev-firmware`。** 这里保留早期 USB 集成实现，不建议用于新设备起步。请优先使用 [`firmware-production`](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) 与当前 [`desktop-app`](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)。

## 当前快照

核对日期 **2026-09-20**，代码基线 **`319b7cb`**（`Add USB Link firmware maintenance mode`）。在早期 HW 2.0 采集固件上加入可选 TinyUSB CDC 控制与 USB 大容量存储维护模式。

包含相机/声音/IMU 采集、TF 存储、Wi-Fi/网页配置和电源管理代码，但早于当前独立的设备身份、状态、配置、事务导出、记录元数据与 USB 会话服务。具备 USB 接口不代表具备现代导出/删除保障或 2.1 配置命令。

## 适用场景

- 查阅原始 CDC/MSC 切换方式与早期软硬件联调。
- 复现仅与该历史快照有关的问题。
- 与当前实现比较设计演进。

历史配套主机程序为 [`old/windows-dev-client-py`](https://github.com/zjmdong/Day_Distiller/tree/old/windows-dev-client-py)，不是旧提交中可能出现的已退役本地分支名称。

## 构建

使用 **ESP-IDF 6.0.1**，先检查本分支的 [`day_pins.h`](main/include/day_pins.h)、[`sdkconfig.defaults`](sdkconfig.defaults) 和 [`main/idf_component.yml`](main/idf_component.yml)：

```sh
git clone --branch old/usb-link-dev-firmware https://github.com/zjmdong/Day_Distiller.git Day_Distiller_Legacy_Firmware
cd Day_Distiller_Legacy_Firmware
idf.py set-target esp32s3
idf.py build
```

配置门户依赖完整 ESP-IDF 源码中的 DNS 示例组件。此快照没有当前固件的主机契约测试集；编译成功不代表实机可靠性已经验证。

## 边界与注意事项

- 使用本分支[协议文档](docs/usb_link_protocol.md)对应的命令集合和接口分配。
- 通过 `HELLO` 探测，不默认沿用当前固件的 CDC 顺序或 capabilities。
- 先备份卡，从只读访问开始；离开 MSC 前先弹出，主机写卡时不要拔线。
- 不使用私人记录或敏感 Wi-Fi 凭据进行历史版本测试，配置接口不是经过加固的认证服务。
- 可参考[当前硬件接口文档](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/HARDWARE.zh-CN.md)了解硬件，但每项假设仍需对照本分支重新确认。

项目由 **JerryZ** 独立开发。原始制造资料保持私有，本分支不新增开源授权；当前项目范围与状态见[主分支](https://github.com/zjmdong/Day_Distiller/tree/firmware-production)。
