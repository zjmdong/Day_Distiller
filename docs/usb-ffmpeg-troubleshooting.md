# USB、TF 卡、MSC 与 FFmpeg 排障

## USB 与 COM 口

1. 使用支持数据传输的 USB 线，避开只充电线和不稳定扩展坞。
2. 打开 Windows 设备管理器，确认设备枚举出协议 CDC。固件 2.0.0 在正常模式把 USB Link 放在 `MI_02`；固件 2.0.1 为避开部分 Windows 主机的第二 CDC 读取故障，改为使用 `MI_00`。进入 MSC 后同样使用 `MI_00`。
3. 在应用“设备”页点击“自动发现”。应用会按照优先级对两个接口执行真实 `HELLO`，因此同时兼容旧固件、2.0.0 和 2.0.1；不要仅根据 COM 口编号判断协议接口。
4. 若驱动异常，先拔插、换线和换 USB 口，再按芯片/板卡厂商说明安装驱动。不要把任意未知驱动包以管理员权限安装。
5. `ClearCommError failed` 通常表示 Windows 保留了刚刚重枚举前的旧 COM 口。等待数秒刷新串口；应用会对真实协议口执行有限重试。若列表仍没有在线端口，请重启设备后重新插拔 USB。

## TF 卡与 MSC

- 使用健康的 FAT32 TF 卡；导入前确认设备端显示 `ready=true`。
- 一键流程先只读挂载，减少 Windows 或应用误写风险。
- 不要在导入过程中拔线、弹盘或强制退出。
- 正常流程使用“安全弹出并退出”；只有卷已经消失且没有 I/O 时才使用“强制退出 MSC”。
- 清理前应用会重扫目录并逐文件比对大小和 SHA-256；任何变化都会放弃删除并保留 `pending_cleanup`。
- Windows 没有分配盘符时，检查磁盘管理、文件系统、卡座接触和盘符冲突。

## FFmpeg

发布目录已经内置 `ffmpeg.exe`、`ffprobe.exe`、许可证和构建说明，用户无需安装或配置。必须保留整个 `DayDistillerClient` 目录；只复制主 EXE 会导致媒体处理组件缺失。

`ffprobe failed` 通常表示 AVI/WAV 损坏、尚未写完或编码不受支持；任务会失败且不会清理设备。先复制原文件留证，再检查设备录制日志。

## 数据目录与空间

默认数据目录是 `%LOCALAPPDATA%\DayDistillerV2`：`imports` 保存原始导入，`cache` 保存关键帧，`reports` 保存单张海报/HTML/PDF，`mock_outbox` 保存测试邮件。首版长期保留这些文件；空间不足时先关闭应用，备份需要的报告，再按日期手动删除旧的 `imports`、`cache` 和 `reports` 子目录。不要删除正在运行任务对应的目录或 SQLite 数据库。

测试时可临时指定：

```powershell
$env:DAY_DISTILLER_DATA_DIR="$env:TEMP\DayDistillerTest"
.\scripts\run.ps1
```

## 恢复原则

API 超时、结构化输出错误、生图失败、Resend 拒绝、程序崩溃和 USB 断开都会保留任务与素材。重新启动后到“报告历史”查看 `failed` 原因并重试。只有数据库存在 Resend 成功回执后，设备清理才可能运行；清理失败单独重试。
