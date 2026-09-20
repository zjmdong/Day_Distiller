# Day Distiller · 固件开发分支

[English](README.md) · **简体中文**

> [!WARNING]
> **当前分支：`firmware-dev`，用于开发。** 项目介绍、公共硬件文档和新用户入口请前往 [`firmware-production`](https://github.com/zjmdong/Day_Distiller/tree/firmware-production)。开发版本不等于已经完成日常使用验证的版本。

[项目介绍](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/README.zh-CN.md) · [硬件文档](docs/HARDWARE.zh-CN.md) · [桌面应用](https://github.com/zjmdong/Day_Distiller/tree/desktop-app) · [分支说明](docs/BRANCHES.md)

## 当前状态

核对日期：**2026-09-20**。此次公开文档更新前，本分支与 `firmware-production` 的代码基线均为 **`87a15cc`**，固件自报版本 **2.1.0**。该检查点的已提交运行代码相同，README 单独标明开发用途。

本地工作区未提交的修复或版本说明，**不代表远端分支已经发布**。确认兼容性时请检查实际提交与设备 `HELLO` 响应；此日期之后两个分支可能继续分化。

## 用途

- 集成采集、传感器驱动、电源管理和网页配置的修改。
- 在进入主固件分支前开发 USB 会话、配置/状态能力与存储导出流程。
- 保留可复现契约测试；将个人日志、凭据和原始硬件设计资料留在本地。

## 构建与测试

使用已激活的 **ESP-IDF 6.0.1** 环境和兼容 **HW 2.0 / ESP32-S3-N16R8** 的硬件：

```sh
git clone --branch firmware-dev https://github.com/zjmdong/Day_Distiller.git Day_Distiller_Dev
cd Day_Distiller_Dev
idf.py set-target esp32s3
idf.py build
python -m unittest discover -s tests -p "test_*.py" -v
```

硬件、烧录与首次采集参阅[上手指南](docs/GETTING_STARTED.zh-CN.md)。`hardware_*.py` 是需主动运行的实机诊断，应先了解其操作并使用可丢弃或已备份的数据，不属于普通主机测试。

## 合入主分支前

- 完成 ESP32-S3 目标构建与主机契约测试。
- 在实机验证采集、休眠/唤醒、低电量与 USB 重新枚举，明确记录实际完成的测试范围。
- 保持 v1 帧协议与已有记录格式，或与桌面端协调明确的兼容性变更。
- 使用测试素材验证导入/导出中断，以及显式删除的边界。
- 脱敏日志，不提交网络配置、个人媒体、原始设计资料或构建产物。

当前 2.1.0 网页配置存在开放热点及明文凭据限制，填写 Wi-Fi 信息前请阅读[配置注意事项](docs/GETTING_STARTED.zh-CN.md#配置与隐私)。

## 作者与范围

由 **JerryZ** 独立开发。不分发原始硬件制造资料，尚未指定项目级软件许可证，详见[主 README](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/README.zh-CN.md#作者与许可)。
