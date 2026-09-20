# Day Distiller · 早期 Windows USB 客户端

[English](README.md) · **简体中文**

> [!WARNING]
> **历史开发分支：`old/windows-dev-client-py`。** 这是早期 Windows USB 维护工具，**不是**当前每日日志应用。新用户请进入 [`desktop-app`](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)，项目介绍见 [`firmware-production`](https://github.com/zjmdong/Day_Distiller/tree/firmware-production)。

## 快照状态

核对日期 **2026-09-20**，代码基线 **`fba0cb2`**（`Improve Python runtime detection for client scripts`），包版本 **0.1.0**。

已实现：

- Python / PySide6 **Windows** 界面，串口发现与连接状态。
- `HELLO`、`GET_STATUS`、`ENTER_MSC`、`EXIT_MSC`。
- 移动磁盘发现、只读/读写选择，以及基于 Windows Shell 的尽力安全弹出。
- 基础协议测试与历史 PyInstaller 打包脚本。

未实现当前 AI 日志流程、媒体分析、海报、SQLite 可恢复任务、现代导出事务、固件 2.1 配置或 Apple Silicon macOS 工作流。

## 运行此快照

仅在研究或复现历史工具时使用。在 Windows 上安装 Python 3.11 后：

```powershell
git clone --branch old/windows-dev-client-py https://github.com/zjmdong/Day_Distiller.git Day_Distiller_Legacy_Client
cd Day_Distiller_Legacy_Client
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m day_distiller_client
.\.venv\Scripts\python.exe -m pytest -q
```

也可使用 `RunClient.bat` / `scripts/run.ps1`。复用已有解释器时，将 `DAY_DISTILLER_PYTHON` 设置为**自己的** Python 路径，不复制开发者的 ESP-IDF 本机安装路径。

历史打包入口为 `scripts/build.ps1`，产物为 `dist/DayDistillerClient/DayDistillerClient.exe`。它使用旧 PyInstaller 流程，不是当前应用的 Nuitka 流程。

## 设备流程与限制

历史配套固件是 [`old/usb-link-dev-firmware`](https://github.com/zjmdong/Day_Distiller/tree/old/usb-link-dev-firmware)。

1. 使用已备份的测试卡与数据线。
2. 自动发现设备，确认串行连接，优先选择**只读**。
3. 进入 MSC，等待 USB 重新枚举后再访问文件。
4. 完成文件操作、确认主机弹出成功，再退出 MSC。

此快照的弹出为**尽力执行**，点击按钮不等于已经证明所有文件写入完成。写卡时不要强制退出或拔线。新设备可能提供本客户端既不理解、也不能保护的额外功能。

由 **JerryZ** 独立开发。本历史分支不新增项目级开源许可证，当前范围与许可说明见[主分支](https://github.com/zjmdong/Day_Distiller/tree/firmware-production)。
