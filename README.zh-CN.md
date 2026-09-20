<div align="center">

# Day Distiller

### 留住片刻，蒸馏一天。

一套将日常生活片段转化为每日记忆的可穿戴相机与桌面应用。

**定制硬件 · 嵌入式系统 · 桌面软件 · 多模态 AI**

[English](README.md) · **简体中文**

[开始使用](docs/GETTING_STARTED.zh-CN.md) · [硬件文档](docs/HARDWARE.zh-CN.md) · [桌面应用](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/README.zh-CN.md) · [工程设计](#工程设计)

</div>

---

## 为什么做 Day Distiller？

忙碌的一天充满经历，却可能在晚上变得难以回忆。手机拍照要求我们在当下判断什么值得记录；写日记依赖事后回忆；持续录像又带来了过多、不易回看的素材。

Day Distiller 尝试寻找中间的可能：胸前佩戴的小型设备间歇记录短暂的画面、声音和运动线索。回到电脑前，桌面应用校验并整理记录，再通过多模态 AI 筛选值得回看的瞬间，生成配有插画海报的 Daily Journal（每日日志）。

**JerryZ 用五个月独立完成了整个系统：** 交互设计、器件选型、两版 PCB、手工焊接组装、嵌入式固件、磁吸外壳的 CAD 建模与制作、桌面应用以及 AI 工作流。第一版暴露出的麦克风装配和摄像头连接器问题，直接推动了第二版 PCB 与外壳的调整。

> **项目状态：** 已打通端到端流程的个人原型，并非经过量产认证的可穿戴产品。当前主分支为 `firmware-production`，已提交固件自报版本为 **2.1.0**；配套桌面应用位于 [`desktop-app`](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)。分支名称不代表长时间稳定性或续航验证已经完成。

## 从生活片段到每日记忆

```mermaid
flowchart LR
    Capture["可穿戴设备：画面、声音、运动"] --> Card["microSD：统一记录包"]
    Card -->|"USB CDC 与大容量存储"| Import["桌面端：导入与校验"]
    Import --> Local["本地媒体与运动分析"]
    Local --> AI["云端 AI：证据分析与瞬间筛选"]
    AI --> Journal["每日日志、海报与邮件"]
```

ESP32-S3 负责采集和存储；电脑负责导入、预处理及历史记录；真实 AI 生成由配置的外部服务完成，**不在设备端运行**。开发时可使用离线 Mock 工作流。

## 已实现的功能

| 层次 | 能力 |
| :--- | :--- |
| **多模态采集** | 五秒记录窗口、定时采集、可选 IMU 唤醒，以及网页手动采集。视频、音频和运动数据共享记录标识与时间参考。 |
| **设备交互** | Wi-Fi 配置门户、相机预览、麦克风波形与 IMU 遥测、配置持久化、RTC/NTP 对时、电池状态和 RGB 灯效。 |
| **存储与功耗** | microSD 记录包、从临时目录到完整记录的提交过程、任务间深度休眠及低电量处理。 |
| **USB 通信** | 带帧边界的 CDC 控制协议、能力协商、只读/读写大容量存储、缓存状态及带 revision 校验的配置更新。 |
| **桌面应用** | Windows 与 Apple Silicon macOS 上的设备识别、按日期导入、大小与 SHA-256 校验、可恢复任务和日志历史。 |
| **每日日志** | FFmpeg 预处理、运动特征、多模态证据分析、瞬间筛选、插画海报、HTML/PDF 与邮件发送。 |

默认配置请求 **1920 × 1080、15 fps** 视频、**16 kHz 单声道**音频及 **104 Hz IMU** 采样。这些是配置目标，不是实测吞吐保证。元数据记录实际帧数、帧率与各数据流的时间信息，用于检查真实表现。

## 工程设计

这个项目的重点不仅是让各个模块工作，更是让它们在接口、状态和失败处理上形成完整系统。

- **可观测的协同采集。** FreeRTOS 任务通过就绪、开始与停止事件协调，共享单调时间基准，并记录各流时间偏移；不把软件协调表述为硬件级同步。见 [`recorder.c`](main/src/media/recorder.c)。
- **完整记录与失败记录分离。** 文件先写入临时目录，完成文件收尾与元数据后，再发布到记录根目录，避免将未完成采集当作成品。见 [`storage_service.c`](main/src/storage/storage_service.c)。
- **跨语言通信契约。** SLIP 帧、CRC32、序列号和 JSON 负载连接 C 固件与 Python 桌面端；通过 `HELLO` capabilities 兼容不同固件。见 [USB 集成指南](docs/USB_INTEGRATION.md)。
- **明确的存储所有权。** 主机与固件不同时写卡；导出清单、复制校验与显式清理事务，将“导入成功”与“允许删除”分开处理。见 [`export_service.c`](main/src/export/export_service.c)。
- **可恢复的软件编排。** SQLite 持久化任务，媒体分析、文本/图像生成与邮件发送通过独立接口组织；确定性 Mock 支持不调用付费服务的验证。见[桌面端源码](https://github.com/zjmdong/Day_Distiller/tree/desktop-app/src/day_distiller_client)。
- **软硬件共同迭代。** 总线划分、唤醒源、相机内存需求、外壳装配与状态反馈放在同一使用流程中设计。见[硬件接口文档](docs/HARDWARE.zh-CN.md)。

## 技术栈

| 领域 | 技术 |
| :--- | :--- |
| 电子与实体设计 | ESP32-S3-WROOM-1-N16R8、OV5640、定制 PCB、EasyEDA、CAD、SLA 3D 打印 |
| 固件 | C、**ESP-IDF 6.0.1**、FreeRTOS、CMake/Ninja、PSRAM、NVS、FATFS |
| 设备接口 | I²C、I²S、SPI、并行相机接口、TinyUSB CDC/MSC、HTTP、WebSocket |
| 桌面应用 | Python 3.11、PySide6、pyserial、SQLite、keyring、Pydantic |
| 媒体与 AI | FFmpeg/FFprobe、NumPy、scikit-learn、Qwen/DeepSeek/Seedream 适配器、Mock providers |
| 测试与构建 | 固件契约测试、桌面 pytest 测试、Nuitka、Windows/macOS 构建工作流 |

## 开始使用

### 没有硬件也可以先了解软件

参阅[桌面应用 README](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/README.zh-CN.md)，从源码启动，打开开发者页面，用自己准备的兼容记录目录运行离线演示。此路径无需设备、AI API Key 或邮件服务；媒体预处理仍需要 FFmpeg/FFprobe。

### 构建固件

安装 [ESP-IDF 6.0.1 / ESP32-S3 开发环境](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/index.html)，在已激活的 ESP-IDF 终端运行：

```sh
git clone --branch firmware-production https://github.com/zjmdong/Day_Distiller.git Day_Distiller
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
```

烧录前先核对[硬件引脚](docs/HARDWARE.zh-CN.md)和[首次启动流程](docs/GETTING_STARTED.zh-CN.md)。固件面向定制 HW 2.0 板；任意 ESP32-S3 相机开发板**不能直接视作兼容替代品**。

### 按目标进入项目

| 想做什么 | 从这里开始 |
| :--- | :--- |
| 搭建兼容硬件 | [硬件概况、总线及 GPIO](docs/HARDWARE.zh-CN.md) |
| 构建、烧录、完成首次记录 | [上手指南](docs/GETTING_STARTED.zh-CN.md) |
| 编写主机程序或扩展 USB 命令 | [USB 集成与协议边界](docs/USB_INTEGRATION.md) |
| 修改采集、配置或功耗逻辑 | [`main/src/`](main/src) 与 [`tests/`](tests) |
| 修改界面、AI 或日志流程 | [`desktop-app`](https://github.com/zjmdong/Day_Distiller/tree/desktop-app) |
| 确认分支用途 | [分支说明](docs/BRANCHES.md) |

## 代码结构

```text
main/
├── include/       引脚、公共类型与服务接口
├── src/
│   ├── board/     板级初始化与共享总线
│   ├── drivers/   电量计、IMU、RTC 与 RGB
│   ├── media/     相机、音频、AVI 写入与采集元数据
│   ├── storage/   microSD 所有权与记录提交
│   ├── usb/       协议帧、命令与维护会话
│   ├── export/    导出清单与显式删除事务
│   └── ...        配置、状态、联网与电源管理
└── web/           嵌入式网页配置界面
docs/              公开的集成与硬件文档
tests/             源码契约测试及需主动运行的实机诊断
```

桌面代码在独立分支，不是当前目录里缺失的子文件夹。切换分支或提交修改前，请先阅读[分支说明](docs/BRANCHES.md)。

## 边界与负责任的使用

- **仍需进一步验证的原型。** 连续记录、续航、反复 USB 中断和日常佩戴体验仍需要更广泛测试；不宣称已经验证全天续航或保证目标帧率。
- **并非完全本地、完全私密。** 真实 AI 模式会向配置的服务商发送选中的媒体与上下文。请检查输入、服务配置和收件人；生成内容可能遗漏或误解细节。
- **仅在可信环境配置。** 当前固件使用开放热点和 HTTP。2.1.0 的网页配置响应包含已保存的 Wi-Fi 密码，不应在不可信客户端附近使用，也不应配置敏感网络。见[配置注意事项](docs/GETTING_STARTED.zh-CN.md#配置与隐私)。
- **尊重被记录的人。** 采用明确、可见的记录方式，在适当情况下征得同意。不要把真实录音、人像、凭据或个人日志提交为测试素材。
- **不分发硬件制造文件。** 公开硬件文档用于兼容实现和固件移植，不包含原始原理图、PCB、Gerber、BOM 或外壳 CAD，也不是可直接投产的制造资料包。

## 作者与许可

**JerryZ** — 项目构思、交互设计、电子硬件、PCB 组装、外壳、固件、桌面应用与 AI 工作流。

仓库尚未指定项目级软件许可证。公开可见不等于开源授权；复用或再分发前请联系作者。第三方组件保留各自许可证，硬件制造源文件保持私有。
