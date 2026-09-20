<div align="center">

# Day Distiller

### 留住片刻，蒸馏一天。

一套将日常片段整理成可视化日记的可穿戴设备与桌面应用。

[English](README.md) · **简体中文**

[项目理念](#项目理念) · [功能](#功能) · [愿景](#愿景) · [开始使用](#开始使用) · [硬件文档](docs/HARDWARE.zh-CN.md) · [桌面应用](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)

</div>

---

## 项目理念

一天里有很多值得记住的瞬间，却常常到了晚上就说不清发生了什么。拿出手机拍照会打断当下的体验；写日记需要事后回忆；持续录像又留下太多难以回看的素材。

Day Distiller 尝试找到一种更轻盈的方式：佩戴的小设备间歇采集简短的画面、声音与运动片段。回到电脑前，桌面应用将片段整理在一起，辅助选出有意义的瞬间，生成配有插画海报的每日日记。

## 功能

| | 可以做什么 |
| :--- | :--- |
| **采集** | 定时或按需留下简短的画面、声音与运动片段。 |
| **设备交互** | 查看相机预览、设备状态和设置。 |
| **导入** | 通过 USB 将记录带入桌面应用，按日期整理。 |
| **每日整理** | 借助 AI 筛选片段，生成每日日记。 |
| **个人风格** | 选择视觉风格和参考形象，制作插画海报。 |
| **回看与分享** | 浏览、编辑过往日记，导出 PDF 或通过邮件分享。 |
| **离线体验** | 使用本地演示了解桌面端流程。 |

## 愿景

这个项目希望成为一种个人记忆伴侣，让普通日子也值得回看。积累下来的片段可以成为一份安静的生活档案：看见当时在意的事，也重新读到那些值得留下的故事。

## 从硬件到软件

从定制电路板、可穿戴外壳到嵌入式固件、桌面应用与 AI 日记体验，整个项目由一人独立设计和开发。当前分支存放固件，配套桌面应用位于 [desktop-app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)。

| 方向 | 技术 |
| :--- | :--- |
| 硬件设计 | EasyEDA、CAD、SLA 3D 打印 |
| 固件 | C、ESP-IDF、FreeRTOS、CMake |
| 桌面端 | Python、PySide6、SQLite、Pydantic |
| 媒体与 AI | FFmpeg、NumPy、scikit-learn、Qwen、DeepSeek、Seedream |
| 构建与测试 | pytest、Nuitka、GitHub Actions |

## 开始使用

固件面向 HW 2.0 定制硬件。在已激活的 [ESP-IDF 6.0.1](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/index.html) 环境中：

```sh
git clone --branch firmware-production https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
```

烧录与首次使用见[上手指南](docs/GETTING_STARTED.zh-CN.md)。桌面端的安装与体验见 [desktop-app README](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/README.zh-CN.md)。

## 文档

| 文档 | 内容 |
| :--- | :--- |
| [上手指南](docs/GETTING_STARTED.zh-CN.md) · [English](docs/GETTING_STARTED.md) | 构建、烧录与首次记录 |
| [硬件文档](docs/HARDWARE.zh-CN.md) · [English](docs/HARDWARE.md) | 兼容设计需要的器件与 GPIO |
| [USB 集成](docs/USB_INTEGRATION.md) | 连接主机应用 |
| [分支说明](docs/BRANCHES.md) | 当前及历史开发分支 |

## 许可

本项目采用 [知识共享 署名—非商业性使用 4.0 国际许可协议](LICENSE.md)（CC BY-NC 4.0）。
