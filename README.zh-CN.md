<div align="center">

# Day Distiller

### 留住片刻，蒸馏一天。

一套将平凡日常的片段，转化为值得回看的故事的可穿戴记忆系统。

**定制硬件 · 嵌入式固件 · 桌面应用 · 多模态 AI**

[English](README.md) · **简体中文**

[项目介绍](#项目介绍) · [功能与体验](#完整的使用体验) · [工程设计](#工程设计) · [开始使用](#开始使用) · [硬件文档](docs/HARDWARE.zh-CN.md) · [桌面应用](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)

</div>

---

## 项目介绍

构成一天的，往往是那些来不及拍下的瞬间。拿起相机会打断当下；写日记需要事后回忆；持续录像虽然留下了素材，却不一定留下了能够回看的记忆。

Day Distiller 尝试另一种节奏：**少量记录，理解一天，留下真正重要的片刻。** 轻巧的可穿戴设备在日常生活中采集短暂的画面、声音和运动信息；回到电脑前，桌面应用将片段汇集起来，辅助发现值得留存的瞬间，并生成每日日记与插画海报。

这些片段最终汇成一条重返当天的线索：再次看见那些容易被日常掩盖的人、地方与小小的发现。

## 完整的使用体验

**佩戴 → 采集 → 存储 → 连接 → 校验 → 蒸馏 → 回看**

| 环节 | 已实现的能力 |
| :--- | :--- |
| **可穿戴设备** | 定时或主动采集简短记录，保留画面、声音和运动信息，支持本地存储、状态查看及设置。 |
| **资料导入** | 通过 USB 连接，按日期浏览记录，并校验导入结果。 |
| **每日日记** | 借助 AI 选出有意义的瞬间，生成日记和插画海报。 |
| **个人空间** | 浏览和编辑历史日记，选择视觉风格，导出 PDF 或通过邮件分享。 |
| **开发体验** | 桌面离线演示，以及面向兼容硬件和主机应用的文档入口。 |

## 工程设计

Day Distiller 将实体设备与数字体验作为一个可运行的完整原型来设计。定制电路、两版 PCB、可穿戴外壳、嵌入式固件、桌面应用和日记工作流均由一人独立完成。此分支包含 HW 2.0 定制硬件的固件；配套应用位于 [desktop-app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)。

- **相互关联的多模态记录。** 画面、声音、运动与时间信息汇入同一次采集，为后续理解片段保留语境。
- **清晰的记录生命周期。** 从采集、本地存储到 USB 导出，记录以可识别的完整单元流转。
- **可靠的交接体验。** 桌面应用校验导入文件、展示进度，并能处理传输中断后的恢复。
- **作为编辑者的 AI。** 日记工作流从单条记录延伸到整日叙事，并为文字搭配可视化海报。
- **软硬件协同设计。** 电路板、外壳、设备交互与桌面应用围绕同一段日常体验共同设计。

接口细节见[硬件文档](docs/HARDWARE.zh-CN.md)、[USB 集成文档](docs/USB_INTEGRATION.md)与[桌面应用](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)。

## 技术栈

| 方向 | 技术 |
| :--- | :--- |
| 电子与工业设计 | EasyEDA、CAD、SLA 3D 打印 |
| 固件 | C、ESP-IDF、FreeRTOS、CMake |
| 桌面端 | Python、PySide6、SQLite、Pydantic |
| 媒体与 AI | FFmpeg、NumPy、scikit-learn、Qwen、DeepSeek、Seedream |
| 测试与分发 | pytest、Nuitka、GitHub Actions |

## 开始使用

**体验桌面应用：** 阅读 [desktop-app 上手说明](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/README.zh-CN.md#快速开始)，使用兼容的示例记录即可体验离线演示。

**构建固件：** 激活 [ESP-IDF 6.0.1（ESP32-S3）](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/index.html) 后运行：

```sh
git clone --branch firmware-production https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
```

烧录和首次使用见[上手指南](docs/GETTING_STARTED.zh-CN.md)。制作兼容设备前，请先查阅[硬件文档](docs/HARDWARE.zh-CN.md)。

## 项目文档

| 文档 | 内容 |
| :--- | :--- |
| [上手指南](docs/GETTING_STARTED.zh-CN.md) · [English](docs/GETTING_STARTED.md) | 构建、烧录与首次记录 |
| [硬件文档](docs/HARDWARE.zh-CN.md) · [English](docs/HARDWARE.md) | 兼容设计需要的器件与 GPIO |
| [USB 集成](docs/USB_INTEGRATION.md) | 连接主机应用 |
| [桌面应用](https://github.com/zjmdong/Day_Distiller/tree/desktop-app) | 界面、日记工作流与应用配置 |
| [分支说明](docs/BRANCHES.md) | 当前及历史开发分支 |

### 仓库结构

- [`main/src/`](main/src) 为固件服务，[`main/web/`](main/web) 为设备交互界面。
- [`tests/`](tests) 为固件测试，[`docs/`](docs) 为集成与硬件文档。
- 桌面端源码、测试和打包脚本位于独立的 [`desktop-app` 分支](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)。

## 未来愿景

Day Distiller 始于一个简单的问题：如果回忆一天不再像整理文件，而更像重新读到一个故事，会是什么样子？这个项目为更轻盈、更私人化的日常记录与回看方式提供了一个起点。

## 许可

本项目采用 [知识共享 署名—非商业性使用 4.0 国际许可协议](LICENSE.md)（CC BY-NC 4.0）。
