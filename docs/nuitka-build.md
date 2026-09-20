# 桌面应用构建

项目使用 Nuitka 为 Windows x64 和 Apple Silicon macOS 打包。先安装 Python 3.11、FFmpeg 和 FFprobe，再在目标平台运行对应脚本。

## Windows

```powershell
.\scripts\build.ps1
```

## Apple Silicon macOS

```sh
./scripts/build_macos.sh
```

打包结果在 dist 目录。也可以查看 [.github/workflows/build-desktop.yml](../.github/workflows/build-desktop.yml) 中的构建工作流。
