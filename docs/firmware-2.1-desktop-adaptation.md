# 固件 2.1 桌面端适配说明

## 兼容策略

桌面端始终先执行 `HELLO`，读取协议版本、固件版本、序列号和 `capabilities`。功能开关只看 capability，不以版本字符串猜测：

| 设备能力 | 桌面端行为 |
| --- | --- |
| 1.x / legacy MSC | 保留 HELLO、GET_STATUS、只读/读写 MSC 与本地导入 |
| `transactional_export_v2` | 使用事务化导出、清理和维护会话 |
| `device_status_v2` | 展示电量、RTC、系统时钟、Wi-Fi、电源和 LED 缓存状态 |
| `device_config_v1` | 读取统一设备配置 |
| `device_config_write` | 启用“保存到设备”，并用 revision 防止覆盖并发修改 |
| `led_preview` | 启用 2 秒临时状态灯预览 |

未知 capability 会被忽略；缺少新能力不会影响一键同步和每日蒸馏。

## 设备设置页面

在“设置 → 设备设置”点击“连接并同步”。页面集中提供：

- 设备身份：固件版本和序列号；
- 状态：电量、RTC 有效性、系统时钟和最后校时来源、Wi-Fi、存储、唤醒/电源、当前 LED 模式；
- 录制：分辨率、JPEG 质量和帧率；
- 配网：SSID 和密码；密码只通过物理 USB 请求，不写日志或普通状态响应；
- 时间：POSIX 时区和 NTP 服务器；
- 自动化：唤醒间隔、自动记录和 5%–39% 低电量阈值；
- RGB：5%–100% 全局亮度、录制颜色和临时预览。

保存使用 `expected_revision`。若设备网页后台在读取后修改了配置，固件应返回 revision 冲突，桌面端不会静默覆盖，用户重新同步后再保存。

## USB 命令

- 12 `GET_CONFIG {"include_secrets":true}`
- 13 `SET_CONFIG {"expected_revision":N,"patch":{...}}`
- 14 `PREVIEW_LED {"color":"#RRGGBB","brightness_percent":5..100,"duration_ms":2000}`

协议仍使用 v1 SLIP 帧、CRC32 和原有命令 1–11。桌面端对 LED 亮度、颜色、分辨率/帧率组合、SSID、密码、NTP、唤醒间隔和低电量阈值执行与固件一致的前置校验。

## macOS

macOS 版本使用 `/Volumes` 和 `diskutil` 发现、等待并安全弹出设备卷；串口仍通过 pyserial 自动发现。应用用 macOS Keychain 保存模型及 SMTP 凭据，并使用内置的 arm64 FFmpeg/FFprobe。
