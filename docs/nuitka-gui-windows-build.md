# 使用 Nuitka GUI 打包 Day Distiller Windows 版

本文适用于当前安装的 **Nuitka GUI v0.6** 和 Day Distiller `desktop-app` 分支。目标产物采用 `standalone` 独立目录模式：启动速度快、问题容易排查，也不会产生 onefile 每次启动时的临时解压开销。

> 不要使用 GUI 默认显示的 Python 3.14.2。当前项目固定使用项目目录中的 Python 3.11 虚拟环境。

## 一、打包前准备

项目目录：

```text
D:\ESP-IDF\Projects\Day_Distiller_Windows_Client
```

打开 PowerShell，执行：

```powershell
cd D:\ESP-IDF\Projects\Day_Distiller_Windows_Client
.\scripts\ensure-env.ps1 -Dev
.\.venv\Scripts\python.exe .\scripts\generate_icon.py `
  .\assets\day-distiller-icon.png `
  .\build\icon\DayDistiller.ico
.\.venv\Scripts\python.exe -m pytest -q
```

只有测试全部通过后再打包。确认下列文件存在：

```text
.venv\Scripts\python.exe
scripts\nuitka_entry.py
build\icon\DayDistiller.ico
assets\day-distiller-icon.png
C:\FFMPEG\bin\ffmpeg.exe
C:\FFMPEG\bin\ffprobe.exe
```

## 二、环境配置

在 GUI 的“环境配置”页填写：

| 项目 | 设置值 |
|---|---|
| 入口脚本 | `D:\ESP-IDF\Projects\Day_Distiller_Windows_Client\scripts\nuitka_entry.py` |
| Python 环境 | `D:\ESP-IDF\Projects\Day_Distiller_Windows_Client\.venv\Scripts\python.exe` |
| 编译器 | `MinGW64` |

操作顺序：

1. 在“入口脚本”右侧点击“浏览”，选择 `scripts\nuitka_entry.py`。
2. 在“Python 环境”右侧点击“浏览”，选择 `.venv\Scripts\python.exe`。
3. 点击“刷新”，确认界面显示的是 Python 3.11，并且带有 Nuitka 标记。
4. 编译器选择 MinGW64。若列表中没有，点击“下载精简编译器”，下载完成后再次刷新。
5. 可点击“测试运行”。Day Distiller 窗口能正常打开后将其关闭，再继续配置。

不要选择系统 Python 3.14，也不要让 GUI 新建另一个虚拟环境，否则会缺少项目依赖或产生不同版本的 PySide6。

## 三、基本设置

在“基本设置”页配置：

| 项目 | 设置值 |
|---|---|
| 编译模式 | `standalone` |
| 输出目录 | `D:\ESP-IDF\Projects\Day_Distiller_Windows_Client\build\nuitka\gui` |
| 输出文件名 | `Day Distiller.exe` |
| 图标文件 | `D:\ESP-IDF\Projects\Day_Distiller_Windows_Client\build\icon\DayDistiller.ico` |
| 编译前清理输出 | 勾选，仅当输出目录严格使用上面的专用目录时 |
| 启用 UAC | 不勾选 |
| 低内存模式 | 不勾选；内存不足时才启用 |
| 全程序优化（LTO） | 不勾选 |
| Windows 控制台 | 禁用/隐藏 |

如果页面下方提供 Windows 版本信息，填写：

| 项目 | 设置值 |
|---|---|
| 产品名称 | `Day Distiller` |
| 文件描述 | `Day Distiller` |
| 公司名称 | `Day Distiller` |
| 文件版本 | `0.3.0.0` |
| 产品版本 | `0.3.0.0` |

不要选 `onefile`。本项目包含 PySide6、科学计算库和 FFmpeg，onefile 不会显著缩小发布包，还会增加每次启动的解压时间并让杀毒软件误报概率升高。

## 四、导入设置

### 4.1 文件与目录

“包含目录”保持为空。

在“包含文件”中逐条添加以下内容。GUI 的格式为 `源文件=目标路径`：

```text
D:\ESP-IDF\Projects\Day_Distiller_Windows_Client\assets\day-distiller-icon.png=assets/day-distiller-icon.png
C:\FFMPEG\bin\ffmpeg.exe=resources/ffmpeg/ffmpeg.exe
C:\FFMPEG\bin\ffprobe.exe=resources/ffmpeg/ffprobe.exe
C:\FFMPEG\LICENSE=resources/ffmpeg/LICENSE
C:\FFMPEG\README.txt=resources/ffmpeg/README.txt
```

如果本机没有 `C:\FFMPEG\LICENSE` 或 `README.txt`，对应两项可以省略；`ffmpeg.exe` 和 `ffprobe.exe` 不能省略。

### 4.2 模块控制

| 项目 | 设置值 |
|---|---|
| 跟踪所有导入 | 勾选 |
| 额外包含的包 | `keyring.backends.Windows` |
| 额外包含的模块 | 留空 |
| 不跟踪的模块 | 如 GUI 支持，添加 `pytest`、`setuptools` |

`keyring.backends.Windows` 是动态加载的 Windows Credential Manager 后端，必须显式包含，否则打包后的程序可能无法读取或保存密钥。

## 五、加速设置

在插件列表中只勾选：

```text
pyside6 - PySide6 GUI支持
```

不要同时勾选 PyQt5、PyQt6、PySide2、Tkinter、Kivy 等其他 GUI 插件。

若页面提供下列选项：

| 项目 | 设置值 |
|---|---|
| LTO | 关闭 |
| PGO | 关闭 |
| 并行编译 | 自动，或设为 CPU 逻辑核心数的一半到四分之三 |
| 删除 docstring / no_docstrings | 开启 |

当前项目以稳定构建和合理耗时优先。关闭 LTO 不会关闭 Nuitka 的 C 编译优化，只是避免耗时和高内存占用的链接时优化。

## 六、预览命令检查

全部设置完成后，先点击“保存配置”，然后点击“预览命令”。预览命令至少应包含以下关键参数：

```text
--mode=standalone
--enable-plugin=pyside6
--windows-console-mode=disable
--windows-icon-from-ico=...DayDistiller.ico
--output-filename=Day Distiller.exe
--output-dir=...build\nuitka\gui
--mingw64
--lto=no
--include-package=keyring.backends.Windows
--include-data-files=...day-distiller-icon.png=assets/day-distiller-icon.png
--include-data-files=C:\FFMPEG\bin\ffmpeg.exe=resources/ffmpeg/ffmpeg.exe
--include-data-files=C:\FFMPEG\bin\ffprobe.exe=resources/ffmpeg/ffprobe.exe
```

命令末尾必须是：

```text
D:\ESP-IDF\Projects\Day_Distiller_Windows_Client\scripts\nuitka_entry.py
```

如果 GUI 没有生成 `--enable-plugin=pyside6`、FFmpeg 两个文件或 `keyring.backends.Windows`，不要开始打包，先返回对应页面补齐。

## 七、开始打包

1. 关闭正在运行的 Day Distiller 测试窗口。
2. 点击“保存配置”。
3. 点击“开始打包”。
4. 首次使用 MinGW64 时可能需要下载编译器；允许下载并等待完成。
5. 编译期间不要关闭 GUI，也不要让电脑休眠。
6. 日志出现成功信息后，打开：

```text
D:\ESP-IDF\Projects\Day_Distiller_Windows_Client\build\nuitka\gui\nuitka_entry.dist
```

找到 `Day Distiller.exe`。整个 `.dist` 目录才是完整程序，不能只复制 EXE。

## 八、整理发布包

GUI 编译完成后，在项目目录运行：

```powershell
$root = 'D:\ESP-IDF\Projects\Day_Distiller_Windows_Client'
$source = Join-Path $root 'build\nuitka\gui\nuitka_entry.dist'
$target = Join-Path $root 'dist\Day Distiller'
$zip = Join-Path $root 'dist\DayDistiller-Windows-x64.zip'

if (Test-Path $target) { Remove-Item $target -Recurse -Force }
Copy-Item $source $target -Recurse
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -LiteralPath $target -DestinationPath $zip -CompressionLevel Optimal
```

最终交付文件：

```text
D:\ESP-IDF\Projects\Day_Distiller_Windows_Client\dist\DayDistiller-Windows-x64.zip
```

## 九、发布前验收

解压 ZIP 到一个全新目录后检查：

1. 双击 `Day Distiller.exe`，不能出现命令行窗口。
2. 任务栏和窗口显示 Day Distiller 图标。
3. 首页动画正常，能进入一键工作流。
4. 设置页能打开，API 与邮件设置可以保存。
5. “设备设置”能识别固件版本和能力；旧固件不支持的选项应自动禁用。
6. 能发现串口、连接设备并进入 MSC 同步流程。
7. 预处理视频时不会弹出 FFmpeg 命令行窗口。
8. 在没有系统 FFmpeg 的电脑上仍能抽帧，证明内置 FFmpeg 生效。
9. 风格预览、日报生成、PDF 和邮件附件路径均正常。

建议同时计算 ZIP 的 SHA-256：

```powershell
Get-FileHash -Algorithm SHA256 .\dist\DayDistiller-Windows-x64.zip
```

## 十、常见问题

### GUI 显示 Python 3.14

重新选择项目中的 `.venv\Scripts\python.exe`。不要继续打包。

### 编译器显示“无编译器”

选择“下载精简编译器”，下载完成后刷新并选择 MinGW64。也可以先在命令行执行一次 `scripts\build.ps1`，让 Nuitka 下载官方缓存编译器。

### 打包成功但程序启动报资源不存在

检查导入设置中是否包含图标、FFmpeg 和 FFprobe，并确认目标路径完全一致。不要只移动 EXE。

### 程序启动后无法保存密钥

确认“额外包含的包”中存在 `keyring.backends.Windows`。

### 启动时出现黑色命令行窗口

将 Windows 控制台模式设为禁用/隐藏，预览命令应出现 `--windows-console-mode=disable`。

### GUI 生成的命令与本文不一致

以项目内 `scripts\build.ps1` 为最终基准。GUI 是第三方前端，版本升级后字段名称或命令生成行为可能变化；开始打包前始终检查“预览命令”。
