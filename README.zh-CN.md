# Day Distiller · 固件开发

[English](README.md) · **简体中文**

> **开发分支：** firmware-dev 用于可穿戴设备固件开发。首次使用请从 [firmware-production](https://github.com/zjmdong/Day_Distiller/tree/firmware-production) 开始。

这里包含当前的采集、设备设置、USB 导入和电源管理代码，也会用于准备主分支的后续修改。

## 构建与测试

使用 ESP-IDF 6.0.1：

```sh
git clone --branch firmware-dev https://github.com/zjmdong/Day_Distiller.git
cd Day_Distiller
idf.py set-target esp32s3
idf.py build
python -m unittest discover -s tests -p "test_*.py"
```

此分支提供[硬件文档](docs/HARDWARE.zh-CN.md)、[上手指南](docs/GETTING_STARTED.zh-CN.md)与[分支说明](docs/BRANCHES.md)，方便开发。桌面应用位于 [desktop-app](https://github.com/zjmdong/Day_Distiller/tree/desktop-app)。

## 许可

此分支采用[知识共享 署名—非商业性使用 4.0 国际许可协议](LICENSE.md)（CC BY-NC 4.0）。
