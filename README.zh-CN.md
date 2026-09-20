<div align="center">

<img src="assets/day-distiller-icon.png" alt="Day Distiller" width="88" />

# Day Distiller Desktop

### 将一天的片段，整理成值得回看的记忆。

Day Distiller 可穿戴设备的配套桌面应用。

[English](README.md) · **简体中文**

[项目首页](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/README.zh-CN.md) · [快速开始](#快速开始) · [硬件文档](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/HARDWARE.zh-CN.md)

</div>

---

## 简介

我做这款桌面应用，是想把设备记录的日常片段放在一起，生成以后愿意翻看的记忆。硬件、固件、桌面界面和 AI 辅助日记都由我独立完成。

## 功能

- 按日期导入、整理记录
- 查看、编辑与重新生成日记
- 制作插画海报和 PDF
- 设置视觉风格与 AI 服务
- 通过邮件分享日记
- 使用离线演示体验完整流程

## 技术栈

Python · PySide6 · SQLite · Pydantic · FFmpeg · NumPy · scikit-learn · pytest · Nuitka

## 快速开始

安装 Python 3.11 与 FFmpeg/FFprobe，单独克隆桌面分支。

**Windows（PowerShell）**

```powershell
git clone --branch desktop-app https://github.com/zjmdong/Day_Distiller.git Day_Distiller_Desktop
cd Day_Distiller_Desktop
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m day_distiller_client
```

**Apple Silicon macOS**

```sh
git clone --branch desktop-app https://github.com/zjmdong/Day_Distiller.git Day_Distiller_Desktop
cd Day_Distiller_Desktop
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m day_distiller_client
```

体验离线演示：进入**设置 → 开发者设置**，选择一份兼容记录的副本，再到生成页选择**离线演示**，保持虚拟卡删除选项关闭。记录文件见[固件上手指南](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/GETTING_STARTED.zh-CN.md)。

使用 AI 和邮件功能时，在应用中填写自己的服务设置。当前界面以中文为主。

## 二次开发

```sh
python -m pytest -q
```

在目标平台使用 scripts/build.ps1（Windows）或 scripts/build_macos.sh（Apple Silicon macOS）构建。其他说明见[用户指引](docs/user-guide.md)、[构建文档](docs/nuitka-build.md)和[排障文档](docs/usb-ffmpeg-troubleshooting.md)。

## 许可

我以 [PolyForm Noncommercial License 1.0.0](LICENSE.md) 发布此分支。
