# 上手指南

[English](GETTING_STARTED.md) · [简体中文](GETTING_STARTED.zh-CN.md) · [项目首页](../README.zh-CN.md)

本文面向 `firmware-production` 已提交版本：HW 2.0、固件 2.1.0、ESP-IDF 6.0.1。仅体验软件可直接进入[桌面分支](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)。

## 1. 准备硬件

- 具有 **16 MB Flash、8 MB Octal PSRAM** 的兼容 ESP32-S3 硬件，并按[硬件文档](HARDWARE.zh-CN.md)核对连接。
- 受支持的相机、麦克风、IMU、RTC、电量计与 RGB 灯，或已完成移植的替代器件。
- 已备份、FAT32 格式的 microSD/TF 卡，以及支持数据传输的 USB 线。
- 安全的电源，以及自己硬件的下载模式/恢复方式说明。

仓库不包含原始制造文件。烧录前检查接线；相机连接器外形相似不代表兼容。

## 2. 安装 ESP-IDF

按[官方 ESP32-S3 指南](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32s3/get-started/index.html)安装 **ESP-IDF 6.0.1**。使用已激活的 ESP-IDF 终端，或通过自己安装目录内的 Windows `export.ps1` / Linux、macOS `export.sh` 激活环境。

```sh
idf.py --version
git clone --branch firmware-production https://github.com/zjmdong/Day_Distiller.git Day_Distiller
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
```

`set-target` 用于新克隆的环境；已有自定义配置时先备份。组件管理器根据 [`main/idf_component.yml`](../main/idf_component.yml) 和 [`dependencies.lock`](../dependencies.lock) 解析依赖，首次构建需要联网。

| 配置 | 位置 |
| :--- | :--- |
| 芯片、Flash 与 PSRAM | [`sdkconfig.defaults`](../sdkconfig.defaults) |
| 板级引脚与采集时长 | [`day_pins.h`](../main/include/day_pins.h) |
| 默认采集参数 | [`app_config.c`](../main/src/config/app_config.c) |
| 配置门户 DNS 组件 | `${IDF_PATH}/examples/protocols/http_server/captive_portal/components/dns_server` |

根 `CMakeLists.txt` 从**完整 ESP-IDF 源码树**引用 DNS 示例组件；缺少 `examples` 的精简安装不够。优先使用锁定版本，不默认兼容其他大版本。

## 3. 烧录

关闭占用串口的软件，必要时按自己硬件的说明进入下载模式。将 `PORT` 换成实际烧录端口，例如 Windows 下的 `COM5`：

```sh
idf.py -p PORT flash
```

烧录会更新设备固件，请先备份需要保留的配置。不要把擦除 Flash 或格式化存储卡当作常规排障步骤。

应用启动后的 TinyUSB 接口可能不同于烧录接口。串行模式下 **CDC0 是二进制协议、CDC1 是日志**；不要向协议串口输入终端文字，也不要让监视器与桌面应用同时占用同一端口。

## 4. 配置与首次采集

1. 插入测试卡。首次 Wi-Fi 配置建议独立供电，避免进入主机 USB 维护会话。
2. 冷启动设备；设备尝试使用已保存 Wi-Fi 对时，再开启配置热点。
3. 及时连接 **`AI_CAM_XXXX`**。初始无人连接窗口约为 **10 秒**；当前提交版本会在客户端连接时延长窗口，但仍有五分钟硬上限。错过后可重新上电。
4. 打开自动弹出的门户；未自动出现时访问热点网关地址，通常为 `http://192.168.4.1`。
5. 检查相机预览、音频波形、IMU、电量与存储；设置时区，仅在合适环境中填写测试网络信息。
6. 从网页触发一次采集，或等待配置阶段结束后的自动采集。默认间隔 **300 秒**，每次 **五秒**，默认关闭运动唤醒。
7. 确认记录包完整后，再测试循环采集与主机导入。

默认时区为 POSIX 格式的 **`CST-8`**，表示 UTC+08:00。请按所在地调整并核对时间。RTC/网络时间未就绪时可能使用回退日期，应检查 `time_quality`，不能只相信目录名称。

### 配置与隐私

热点开放，配置接口使用 HTTP，且没有用户认证边界。**固件 2.1.0 的网页配置响应会返回保存的 Wi-Fi 密码。** 请在隔离测试环境使用，避免保存敏感网络凭据，也不要通过路由或端口转发向外暴露该接口。这属于运行时限制，与 Git 仓库是否含凭据是两件事。

USB 配置/状态接口有独立的能力协商和秘密读取规则，不能据此认为网页接口同样受到保护。开发者工作区里尚未提交的修复，不属于本文描述的版本。

## 5. 理解记录格式

已完成记录位于存储卡根目录：

```text
REC_0001_260920_143000/
├── video.avi      MJPEG 视频
├── audio.wav      单声道 PCM s16le 音频
├── imu.json       运动采样与时间信息
└── meta.json      记录标识、时间质量与各流结果
```

格式为 `REC_NNNN_YYMMDD_HHMMSS`。`NNNN` 是四位序号片段，不是全局唯一标识；跨记录关联请使用元数据中的 `record_id` 与 `device_id`。

正在写入的文件位于 `.recording/REC_....partial`，文件与元数据收尾后才发布到根目录。不要把 `.partial` 当作成品导入，也不要在排障时自动删除它。

当前元数据 `schema_version` 为 `2`，需要重点检查：

- `record_state` 与 **`capture_result`**：已提交记录仍可能标记 `partial_stream_failure`，目录发布不代表每个传感器都成功。
- `time_quality`、`start_time_utc_ms`、`local_time`、`timezone`。
- `streams.video.actual_fps`、样本数量、各数据流错误与时间偏移。

视频、声音与 IMU 使用软件协调。应检查实测偏移和速率，不默认采样级同步或达到目标帧率。

## 6. 连接桌面应用

建议单独克隆，避免固件和桌面环境互相影响：

```sh
git clone --branch desktop-app https://github.com/zjmdong/Day_Distiller.git Day_Distiller_Desktop
```

按[桌面端说明](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/README.zh-CN.md)配置，先用离线 Mock 与可丢弃输入验证流程。

连接实机时，应用识别协议接口、请求存储模式切换并校验复制文件。USB 重新枚举可能改变串口名称。退出 MSC 前务必安全弹出，切换期间不要让其他进程写卡。真实云端生成与邮件服务需要额外配置，并可能产生费用。

## 7. 二次开发与验证

无需硬件即可运行固件源码契约测试：

```sh
python -m unittest discover -s tests -p "test_*.py" -v
```

这些测试检查源码层面的契约，不模拟 ESP32 时序，不能证明电气行为，也不能替代 ESP-IDF 构建。`tests/hardware_*.py` 为需主动运行的实机工具，**不属于默认测试发现范围**；使用前查看其帮助与具体操作，并准备已备份或可丢弃的数据。

| 修改方向 | 需要一起检查 |
| :--- | :--- |
| 新板/新传感器 | 引脚、初始化、驱动、电源状态与采集元数据 |
| 采集格式 | 记录器、元数据、存储提交、桌面导入及预处理 |
| USB 命令 | C 协议/会话、Python 协议/设备实现、两端测试 |
| 配置项 | 默认值、校验、持久化/revision、网页与桌面能力判断 |
| AI 或日志行为 | 桌面 provider 接口、任务状态、Mock 与报告生成 |

## 排障入口

| 现象 | 优先检查 |
| :--- | :--- |
| 相机或 PSRAM 初始化失败 | 模组型号、Octal PSRAM 配置、电源、RESET 与 FPC 顺序 |
| 找不到热点 | 是否冷启动、保存网络的对时过程、门户超时、USB 维护状态 |
| TF 卡挂载失败 | 先备份，再检查文件系统、SPI 与供电；固件刻意禁止自动格式化 |
| 多个串口 | 区分协议、日志与下载接口，让桌面端通过 `HELLO` 探测 |
| 记录日期异常 | RTC、NTP、时区与 `meta.json` 的时间质量 |
| 帧率低于配置 | 实际元数据、光照/JPEG 负载、PSRAM 与 SPI 卡吞吐 |
| 桌面无法处理媒体 | FFmpeg/FFprobe、输入有效性，以及[桌面排障文档](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/docs/usb-ffmpeg-troubleshooting.md) |

反馈问题时提供分支/提交、固件版本、硬件差异、复现步骤和**脱敏**日志，不要附上 API Key、网络配置、可识别个人的录制素材或私有硬件源文件。
