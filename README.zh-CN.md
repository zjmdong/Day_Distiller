<div align="center">

# Day Distiller

### 留住片刻，蒸馏一天。

一套把日常片段整理成可视化日记的可穿戴设备与桌面应用。

[English](README.md) · **简体中文**

[开始使用](docs/GETTING_STARTED.zh-CN.md) · [硬件文档](docs/HARDWARE.zh-CN.md) · [桌面应用](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)

</div>

---

## 项目简介

我做 Day Distiller，是想用更轻松的方式留住日常生活中值得回看的瞬间。从可穿戴硬件、固件到桌面应用，我独立完成了设计与开发。

设备采集简短的画面、声音和运动片段；桌面应用整理这些记录，生成每日日记和插画海报。

## 功能

- 定时与手动采集短片段
- 相机预览、设备设置与状态查看
- USB 导入与按日期整理记录
- AI 辅助筛选片段并生成每日日记
- 插画海报、PDF 导出与邮件分享
- 无需设备即可体验的离线演示

## 技术栈

| 方向 | 工具与技术 |
| :--- | :--- |
| 硬件设计 | EasyEDA、CAD、SLA 3D 打印 |
| 固件 | C、ESP-IDF、FreeRTOS、CMake |
| 桌面端 | Python、PySide6、SQLite、Pydantic |
| 媒体与 AI | FFmpeg、NumPy、scikit-learn、Qwen、DeepSeek、Seedream |
| 构建与测试 | pytest、Nuitka、GitHub Actions |

## 开始使用

此分支的固件面向我的 HW 2.0 定制硬件。在已激活的 [ESP-IDF 6.0.1](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/index.html) 环境中：

```sh
git clone --branch firmware-production https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
```

烧录与首次使用见[上手指南](docs/GETTING_STARTED.zh-CN.md)。复刻或移植时可查阅[硬件器件与引脚文档](docs/HARDWARE.zh-CN.md)。

配套桌面应用位于独立的 [desktop-app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app) 分支，安装与离线演示见该分支的 README。

## 文档

- [上手指南](docs/GETTING_STARTED.zh-CN.md) · [English](docs/GETTING_STARTED.md)
- [硬件与 GPIO](docs/HARDWARE.zh-CN.md) · [English](docs/HARDWARE.md)
- [USB 集成](docs/USB_INTEGRATION.md)
- [分支说明](docs/BRANCHES.md)

## 许可

我以 [PolyForm Noncommercial License 1.0.0](LICENSE.md) 发布本项目。非商用使用、修改和分发请遵循许可证条款。
