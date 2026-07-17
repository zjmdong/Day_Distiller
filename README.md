# Day Distiller 固件

本仓库包含 Day Distiller HW 2.0 的 ESP-IDF 固件。固件 2.1.0 延续 USB Link 帧协议 v1，并在保持设备身份、MSC、录制文件和事务导出兼容的基础上增加设备配置、缓存状态、RGB 状态灯和新的启动/电源状态机。

## 兼容性承诺

- USB 帧协议继续使用版本 1，命令 1–11 的编号和既有行为不变。
- USB VID 为 `0x303A`；串行模式 PID 为 `0x4020`，MSC 模式 PID 为 `0x4021`。
- CDC0 用于协议，CDC1 用于日志。
- 保持现有录制目录、文件名、metadata schema v1/v2 和事务导出格式兼容。
- 失败、断连或中止不会隐式删除设备记录；只有完整校验后的显式删除提交才能删除已冻结清单中的记录。
- Wi-Fi 密码不会写入日志或普通状态响应，`GET_STATUS` 只读取缓存且不会启动传感器。

## 更新日志

### 2.1.0

- 统一 NVS 配置服务：迁移旧键，使用 revision 做乐观并发控制，并保证提交失败时不污染内存配置。
- 新增缓存式设备状态服务，覆盖电池、RTC、系统时间、Wi-Fi、传感器、存储、电源和 LED；USB 状态读取不执行现场 I/O。
- 在协议 v1 上增量加入设备配置读取/写入和 LED 预览命令；通过 HELLO capabilities 协商启用，旧桌面端不受影响。
- 增加严格的字段范围、UTF-8/JSON、秘密读取门控和最大响应帧校验。
- RGB 灯改为单任务控制器，支持优先级状态、30 FPS 呼吸动画、可配置亮度/录制颜色和 2 秒易失性预览。
- 重构冷启动、USB 维护、MSC 恢复、Wi-Fi/NTP 和网页门户路径：联网总预算受限，无客户端门户约 10 秒退出，USB HELLO 可抢占耗时等待。
- 自动唤醒录制完成后读取电量：读取失败正常休眠，低于 40% 橙色提示，低于用户阈值时提示后进入无唤醒源的锁定休眠。
- 低电量锁定使用带 magic/schema/CRC 的 RTC 保留状态；深睡、软件复位和看门狗复位不清除，真实断电上电清除。
- 桌面端新增 capability-gated 设备设置页面和 LED 预览；密码不写入桌面普通配置或日志。

实现里程碑：

| 里程碑 | 提交 |
| --- | --- |
| 统一配置与缓存状态 | `37719bc` |
| USB 配置命令与状态字段 | `4b9fccc` |
| 单任务 RGB 控制器与 LED 预览 | `8501d0a` |
| 启动/门户状态机与低电量锁定 | `3af48e0` |

### 2.0.1

- 修复收到 HELLO 后协议任务栈溢出并重启的问题。
- 协议迁移至 CDC0，日志迁移至 CDC1。
- 协议收发大缓冲区迁移至堆内存，协议任务栈由 4096 增至 8192。

### 2.0.0

- 保持 USB Link v1 命令 1–5、VID/PID、MSC 和既有录制文件名兼容。
- 增加能力发现和可持续 USB 维护会话。
- 增加多日期导出清单、可恢复精确删除、原子记录发布和 metadata schema v2。
- 删除采用显式提交策略；中止导出只关闭事务，不删除原始记录。

## 2.1.0 验证摘要

- ESP-IDF 6.0.1 clean build 通过；应用镜像大小 `0x1448e0`，应用分区剩余约 13%。
- 固件契约测试 62/62 通过；桌面端测试 65/65 通过。
- 实机 HELLO 与 36 次请求压力回归通过，固件报告 `2.1.0`，最大响应延迟 56.14 ms。
- 实机配置/LED 回归 22 项通过，原配置已恢复，记录清单未变化，日志未发现密码或崩溃信息。
- 实机只读 MSC 共存回归 12 项通过；Windows 安全弹出后正常返回串行维护模式。
- 本次验证未调用记录删除命令。完整 50 次自动唤醒循环和受控低电量放电矩阵仍应作为量产硬件验收项目执行。

## 构建与测试

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
$env:IDF_TOOLS_PATH = 'D:\ESP-IDF\.espressif'
. 'D:\ESP-IDF\v6.0.1\esp-idf\export.ps1'
idf.py fullclean build
python -m unittest discover -s tests -p 'test_*.py' -v
```

## 相关文档

- [固件 2.1 实施计划](docs/firmware_v2_1_implementation_plan_zh.md)
- [固件 2.0 实施计划](docs/firmware_v2_implementation_plan_zh.md)
- [USB Link 协议 v1](docs/usb_link_protocol.md)
- [USB Link 协议扩展草案](docs/usb_link_protocol_v2_draft.md)
