# Nuitka 构建与发布

## Windows x64

要求 Python 3.11 和可从 PATH 发现的 FFmpeg/FFprobe：

```powershell
.\scripts\build.ps1
```

脚本创建/更新 `.venv`，运行 Nuitka standalone 编译，启用 PySide6 插件、LTO、无控制台 GUI 模式和无 docstring 优化，并把图标、FFmpeg、FFprobe及许可证放入应用目录。最终分发 `dist\DayDistiller-Windows-x64.zip`，不能只复制 EXE。

## Apple Silicon macOS

macOS 应用不能从 Windows 交叉编译。请在 M1/M2/M3/M4 或后续 Apple Silicon 机器上安装官方 arm64 CPython 3.11 与 FFmpeg，然后执行：

```bash
python3 -m pip install -e '.[dev]'
./scripts/build_macos.sh
```

脚本会拒绝 x86_64 主机，显式指定 `--macos-target-arch=arm64`，构建 `.app` 后再次用 `file` 检查可执行文件包含 arm64，并执行 ad-hoc codesign。发布到其他用户前仍建议使用 Apple Developer ID 正式签名并公证。

`.github/workflows/build-desktop.yml` 使用 GitHub 的 M1 arm64 macOS runner，分别生成 Windows x64 和 macOS Apple Silicon 构建产物。FFmpeg 是两个平台体积最大的组件；为保证无需用户安装和一致的媒体处理能力，生产包仍完整内置。
