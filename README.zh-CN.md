<div align="center">

<img src="assets/day-distiller-icon.png" alt="Day Distiller" width="88" />

# Day Distiller Desktop

### 为一天里值得记住的片刻，留一个位置。

Day Distiller 可穿戴设备的配套桌面应用。

[English](README.md) · **简体中文**

[项目首页](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/README.zh-CN.md) · [功能](#功能) · [快速开始](#快速开始) · [硬件文档](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/HARDWARE.zh-CN.md)

</div>

---

## 简介

可穿戴设备收集日常生活的短暂片段，桌面应用让它们有了可以回看的地方：连接设备、选择日期、挑选视觉风格，最终得到一篇日记和一张插画海报。

这个项目希望让回忆一天变得从容，像翻看一段故事，而不是整理一堆录制文件。桌面应用与可穿戴硬件、固件、AI 工作流一起，组成一套由一人独立开发的完整作品。

## 功能

| 页面与内容 | 体验 |
| :--- | :--- |
| **开始** | 引导连接、导入记录、生成当日日记。 |
| **回忆** | 按日期浏览、编辑和重新生成日记。 |
| **形象与风格** | 设置参考形象、视觉风格并预览海报效果。 |
| **分享** | 插画海报、PDF 和邮件。 |
| **设置** | 设备选项、AI 服务及离线演示。 |

## 技术栈

Python · PySide6 · SQLite · Pydantic · FFmpeg · NumPy · scikit-learn · pytest · Nuitka

## 快速开始

安装 Python 3.11 和 FFmpeg/FFprobe，再克隆桌面分支。

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

没有设备或云端服务时，可进入**设置 → 开发者设置**，选择一份兼容记录的副本，在生成页开启**离线演示**，保持虚拟卡删除选项关闭。记录文件说明见[固件上手指南](https://github.com/zjmdong/Day_Distiller/blob/firmware-production/docs/GETTING_STARTED.zh-CN.md)。

日常使用时，通过 USB 连接设备，导入某一天的记录，再从“开始”页生成日记。AI 与邮件服务可在设置中添加。当前界面以中文为主。

## 二次开发与文档

```sh
python -m pytest -q
```

在目标平台使用 scripts/build.ps1（Windows）或 scripts/build_macos.sh（Apple Silicon macOS）构建。

- [用户指引](docs/user-guide.md)
- [AI 服务配置](docs/mainland-model-setup.md) · [邮件配置](docs/smtp-setup.md)
- [构建文档](docs/nuitka-build.md) · [排障文档](docs/usb-ffmpeg-troubleshooting.md)

## 许可

此分支采用[知识共享 署名—非商业性使用 4.0 国际许可协议](LICENSE.md)（CC BY-NC 4.0）。
