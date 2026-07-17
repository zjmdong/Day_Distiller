# Day Distiller 固件 2.1.0 完整更新计划书

文档状态：可执行实施基线  
编写日期：2026-07-17  
固件基线：`codex/firmware-v2` / `0dbce23` / firmware `2.0.1`  
桌面端基线：`codex/desktop-sync-production-fix-v2` / `29c059a`  
目标版本：firmware `2.1.0`  

## 1. 版本目标

2.1.0 不是对 2.0.1 的推倒重写，而是在保持录制文件、USB 帧格式、事务化导出和现有网页兼容的前提下，完成四项升级：

1. 通过 USB 向桌面端提供可信、非阻塞的设备状态，包括电量、RTC、当前系统时钟、最后校时来源、Wi-Fi、传感器、存储、电源和 LED 状态。
2. 允许网页端和桌面端读取、校验并保存录制、Wi-Fi、时间、自动化和 RGB 灯配置。
3. 重构开机、USB 维护、网页配网、自动录制、低电量提示和休眠之间的状态机，使每条路径都有明确超时和优先级。
4. 修复当前审计发现的启动耗时、状态读取副作用、LED 并发、时间可信度、配置解析和低电量策略问题。

## 2. 不可破坏的兼容性约束

- USB SLIP 帧版本继续为 `1`，保留 20 字节头、CRC32、2048 字节帧上限和 1600 字节 JSON 上限。
- 命令 ID 1–11、状态码 0–8、VID/PID、CDC0 协议口、CDC1 日志口和 MSC 描述符保持兼容。
- `HELLO`、`PING`、`GET_STATUS`、`ENTER_MSC`、`EXIT_MSC` 和事务化导出行为不得退化。
- 保留 `REC_XXXX_YYMMDD_HHMMSS`、`video.avi`、`audio.wav`、`imu.json`、`meta.json` 和 metadata schema 1/2 的读取兼容性。
- 2.0.1 桌面端连接 2.1.0 时，自动发现、只读同步、读写手动挂载、事务删除和安全退出必须继续工作。
- 所有新增能力通过 `capabilities` 协商；桌面端不得仅按版本号启用功能。
- 任意配置、网络、状态或 LED 功能失败，都不得删除、覆盖或损坏已有录制。

## 3. 当前实现审计结论

### 3.1 已有能力

当前 `day_config_t` 和网页后台已经具备大部分基础字段：

- 录制/预览分辨率、JPEG 质量、预览/录制帧率；
- Wi-Fi SSID 和密码；
- 时区和 NTP 服务器；
- 自动唤醒间隔、自动记录、摇晃唤醒和低电量阈值；
- 电池、RTC、Wi-Fi、相机、音频、IMU 和 TF 卡的网页状态；
- RGB 灯基础驱动和固定模式；
- USB 2.0 事务化导出和维护会话。

因此 2.1.0 的重点是统一配置/状态服务、补齐 USB 接口、增加 LED 配置和重做启动/电源状态机，而不是复制一套平行逻辑。

### 3.2 必须修复的问题

| 问题 | 当前风险 | 2.1.0 处理 |
| --- | --- | --- |
| `collect_status()` 会现场读 I²C、刷新 FAT，甚至启动麦克风 | HELLO/状态读取可能阻塞或干扰录制 | 使用缓存式 `device_status_service`，USB 只序列化快照 |
| `day_board_init()` 每次启动扫描全部 I²C 地址 | 最坏可增加数秒启动时间 | 全总线扫描只在调试构建启用，生产版仅探测已知地址 |
| Wi-Fi/NTP 三轮同步最坏可能阻塞一分钟以上 | 电脑连接、配网和录制被拖延 | 增加总预算、可取消操作和阶段性超时 |
| USB 在 NVS、LED 和状态服务之前启动 | 主机可能在服务未就绪时请求配置，且无法及时显示 USB 灯效 | 调整最小初始化顺序，并允许状态返回 `available:false` |
| LED 由多个任务直接调用 `fill_all()` | 灯效互相覆盖、闪烁顺序不确定 | 单一 LED 控制任务、命令队列、优先级和临时效果恢复栈 |
| Web 配置用字符串查找解析 JSON | 转义字符、重复键、异常数字可能被误解析 | 全部改为 cJSON 严格解析和字段白名单 |
| NVS 保存忽略单个 `nvs_set_*` 的失败 | 页面可能显示成功但部分字段未保存 | 聚合每一步错误，只在全部写入成功后 commit |
| RTC 只要日历字段合法就视为可信 | 已实机出现 `2070-01-01` 记录 | 引入可信校时标记和来源，未受信时间不得作为有效 RTC |
| 低电量在录制前直接跳过记录 | 与新版“完成记录后提示”要求冲突 | 自动唤醒路径改为录制后采样、提示并决定锁定休眠 |
| `auto_record_enabled=false` 时仍周期唤醒 | 无意义耗电 | 不再配置定时唤醒；保留显式允许的其他唤醒源 |
| Web 通过开放热点返回明文 Wi-Fi 密码 | 附近设备可能读取家庭 Wi-Fi 密码 | 明文只允许物理 USB读取；网页默认返回“已设置”而非原文 |

## 4. 目标架构

```text
NVS / battery / RTC / Wi-Fi / recorder / storage / sensors
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
  device_settings_service     device_status_service
  - 版本迁移与默认值           - 周期刷新与时间戳
  - 严格校验                   - 无副作用快照
  - 原子保存与 revision        - 模块健康和错误码
  - 运行时应用                 - 串行/MSC 稳定 schema
            │                         │
      ┌─────┴─────┐             ┌─────┴─────┐
      ▼           ▼             ▼           ▼
   Web API     USB Config    Web Status   USB Status

                         led_controller
                  - 单任务、队列、优先级
                  - 全局亮度与录制颜色
                  - USB/配网/电量临时灯效
```

建议新增：

- `main/include/device_settings.h`
- `main/src/config/device_settings.c`
- `main/include/device_status.h`
- `main/src/device/device_status.c`
- `main/include/time_state.h`
- `main/src/device/time_state.c`
- `main/include/boot_state.h`
- `main/src/power/boot_state.c`

`app_core.c` 只负责阶段编排，不再直接拥有所有缓存和配置副作用。

## 5. 配置模型与校验

### 5.1 对外配置 schema

网页和 USB 使用同一份逻辑配置：

```json
{
  "schema_version": 1,
  "revision": 7,
  "video": {
    "record_framesize": 16,
    "jpeg_quality": 12,
    "record_fps": 15
  },
  "wifi": {
    "ssid": "Home WiFi",
    "password": "example-password",
    "password_set": true
  },
  "time": {
    "timezone": "CST-8",
    "ntp_server": "ntp1.aliyun.com"
  },
  "system": {
    "wake_interval_sec": 300,
    "auto_record_enabled": true,
    "low_battery_percent": 20
  },
  "led": {
    "brightness_percent": 100,
    "recording_color": "#FF3000"
  }
}
```

保留当前网页的预览分辨率、预览帧率、音频和 IMU 高级设置，但生产桌面端 2.1 首批只暴露用户要求的字段。USB schema 可以保留扩展位置，不能出现两个互相独立的配置真相源。

### 5.2 校验规则

| 字段 | 规则 |
| --- | --- |
| `record_framesize` | 仅允许 10/VGA、11/SVGA、13/HD、15/UXGA、16/FHD |
| `jpeg_quality` | 固件接受 4–63；UI 首选 10/高、12/标准、18/轻量、25/省空间 |
| `record_fps` | 仅允许 5、10、12、15、20、24、30，且不得超过对应分辨率上限 |
| VGA/SVGA/HD/UXGA/FHD 最大 FPS | 分别为 30/20/15/10/15 |
| `ssid` | UTF-8 字节长度 0–32；空字符串表示清除配网 |
| `password` | UTF-8 字节长度 0–63；字段缺失表示不修改，显式空字符串表示清除 |
| `timezone` | 1–32 字节、可打印 POSIX TZ；UI 提供白名单选项 |
| `ntp_server` | 1–64 字节，只允许主机名或 IP，不接受 URL、路径和控制字符 |
| `wake_interval_sec` | 60–86400 秒 |
| `auto_record_enabled` | 严格 JSON boolean |
| `low_battery_percent` | 5–39；必须低于固定的 40% 提醒线 |
| `brightness_percent` | 5–100；输入低于 5 必须返回字段错误，不能静默保存为 0 |
| `recording_color` | `#RRGGBB`；不允许 `#000000`，确保录制提示可见 |

加载旧 NVS 时可以规范化历史异常值；来自 Web/USB 的新写入必须明确拒绝并返回字段级错误，不能悄悄改值。

### 5.3 NVS 兼容与原子性

- 保留 `day_cfg` 命名空间和现有键，避免升级后丢失用户配置。
- 新增 `cfg_schema=2`、`cfg_revision`、`led_bri`、`led_rec_r/g/b`。
- LED 默认亮度为 100%，默认录制颜色为 `#FF3000`，以保持 2.0.1 视觉行为。
- 保存时先验证完整候选配置，再执行全部 `nvs_set_*`，任一失败则不报告成功。
- `nvs_commit()` 成功后才更新内存中的 revision 和当前配置快照。
- 配置服务使用 mutex；Web 和 USB 同时保存时不会产生撕裂配置。
- 可选 `expected_revision` 用于桌面端避免覆盖网页刚保存的修改；冲突返回 `BAD_STATE` 和 `reason:"revision_conflict"`。

### 5.4 运行时生效规则

- LED 亮度和录制颜色：保存成功后立即生效。
- 时区：立即 `setenv("TZ")` + `tzset()`，不强制联网。
- NTP、SSID、密码：保存成功，下次冷启动使用；网页“保存并连接”可显式触发测试。
- 分辨率、质量、帧率：录制/预览空闲时生效；录制中返回 `BUSY`，不热切换相机。
- 自动记录、唤醒间隔、低电量阈值：下次进入休眠时生效。

## 6. USB 协议 2.1 增量

### 6.1 新命令

保持命令 1–11 不变，追加：

| ID | 命令 | 有效模式 | 用途 |
| ---: | --- | --- | --- |
| 12 | `GET_CONFIG` | serial maintenance | 读取设备配置 |
| 13 | `SET_CONFIG` | serial maintenance、非录制 | 原子应用配置 patch |
| 14 | `PREVIEW_LED` | serial maintenance、非录制 | 临时预览颜色/亮度，不写 NVS |

`GET_CONFIG` 请求：

```json
{"include_secrets":true}
```

- `include_secrets:false` 或缺失时不返回密码原文，只返回 `password_set`。
- `include_secrets:true` 仅允许物理 USB 串行维护模式；密码不得写入日志、状态 JSON 或错误文本。

`SET_CONFIG` 请求采用 patch 语义：

```json
{
  "expected_revision":7,
  "patch":{
    "video":{"record_framesize":13,"jpeg_quality":12,"record_fps":15},
    "system":{"wake_interval_sec":600,"auto_record_enabled":true},
    "led":{"brightness_percent":35,"recording_color":"#00A6FF"}
  }
}
```

响应：

```json
{"ok":true,"revision":8,"applied":["video","system","led"],"reboot_required":false}
```

`PREVIEW_LED` 请求：

```json
{"color":"#00A6FF","brightness_percent":35,"duration_ms":2000}
```

- `duration_ms` 限制 250–5000。
- 预览结束后恢复此前状态，不得把正在录制、低电量或错误灯效覆盖掉。

### 6.2 新 capabilities

```json
[
  "device_status_v2",
  "battery_status",
  "rtc_status",
  "wifi_status",
  "power_status",
  "device_config_v1",
  "device_config_write",
  "config_secrets_over_usb",
  "led_settings",
  "led_preview"
]
```

### 6.3 状态响应分层

- `HELLO`：保留 2.0.1 的身份、版本、模式、storage 和 capabilities 字段，用于快速协商。
- `PING`：保留兼容字段，但不现场刷新传感器。
- `GET_STATUS`：在兼容字段上追加完整缓存状态。
- 三者都必须在 100 ms 内完成；任何驱动读取都不得发生在 USB 协议任务中。

建议 `GET_STATUS` 追加：

```json
{
  "battery": {
    "available": true,
    "soc_percent": 76.4,
    "voltage_v": 3.91,
    "charge_state": "estimated",
    "confidence": 70,
    "sample_age_ms": 420,
    "last_error": 0
  },
  "rtc": {
    "available": true,
    "valid": true,
    "unix_time": 1784275200,
    "iso8601": "2026-07-17T13:20:00+08:00",
    "sample_age_ms": 310,
    "last_error": 0
  },
  "clock": {
    "system_valid": true,
    "timezone": "CST-8",
    "source": "rtc_restore",
    "last_sync_source": "ntp",
    "last_sync_unix": 1784271000
  },
  "wifi": {
    "available": true,
    "configured": true,
    "sta_connected": false,
    "ap_running": false,
    "ap_clients": 0,
    "time_synced": false,
    "ssid": "Home WiFi",
    "ip": "",
    "rssi": null,
    "last_error": 0
  },
  "power": {
    "wake_reason": "timer",
    "reset_reason": "deep_sleep",
    "low_battery_latched": false,
    "timer_wake_enabled": true,
    "next_wake_sec": 300
  },
  "led": {
    "mode": "usb_handshake",
    "brightness_percent": 35,
    "recording_color": "#00A6FF"
  }
}
```

状态规范：

- 模块未初始化也保留对象，使用 `available:false`、`last_error` 和 `null`，不得伪造 0 值。
- RTC 无效时 `unix_time`、`iso8601` 为 `null`，不能返回 `1970`、`2070` 或字符串 `invalid` 冒充时间。
- `charge_state` 固定为 `unknown`、`not_charging`、`estimated`；当前硬件没有独立充电检测脚，不能宣称精确充电状态。
- Wi-Fi 密码绝不进入 `GET_STATUS`。
- CI 必须构造最长 SSID、最长能力列表和所有状态对象，验证序列化结果 `<1600` 字节；超限必须测试失败，不能静默截断。

## 7. 缓存式设备状态服务

### 7.1 刷新策略

- 电池：设备清醒时每 2 秒刷新；自动录制结束后强制刷新一次。
- RTC：每 1 秒刷新；写入 RTC 后立即刷新。
- Wi-Fi：事件回调更新连接/AP/客户端状态；RSSI 最多每 2 秒刷新。
- 存储：挂载、录制完成、导出状态变化时刷新；普通 GET_STATUS 不调用 FAT 容量扫描。
- 相机/音频/IMU：只读取各驱动已有的最后状态，不为状态页面启动设备。
- 每个快照包含 `sample_age_ms` 和稳定整数错误码。

### 7.2 并发要求

- 快照读写使用短临界区或 mutex；序列化先复制快照，再释放锁。
- 录制期间不得读取会占用相机、I²S、TF SPI 或长时间占用 I²C 的诊断信息。
- MSC 快速启动至少初始化 NVS、状态 schema、电池和 RTC；无法安全初始化的模块返回 `available:false`。
- 维护模式持续读取 30 分钟不得产生堆增长。

## 8. 时间可信度与校时来源

新增持久化时间状态：

- `trusted_time_set`；
- `last_sync_source`：`ntp`、`factory`、`none`；
- `last_sync_unix`；
- 校验 magic/schema/CRC。

运行时 `clock.source` 可为：

- `ntp`：本次启动已完成 NTP；
- `rtc_restore`：系统时钟由一个曾被可信来源写入的 RTC 恢复；
- `factory`：生产或维修流程明确写入的可信时间；
- `untrusted`：没有可信时间。

只有“PCF8563 日历/低压位有效”并且“设备曾由可信来源写入 RTC”同时满足时，`rtc.valid=true`。这用于阻止未初始化或异常 RTC 产生 `2070-01-01` 一类记录。

未受信时钟不得用于声称真实日期：metadata 写入 `time_quality:"untrusted"`，使用可唯一化的安全回退时间命名，并在后续桌面端显示“时间未校准”。不得覆盖同名目录。

## 9. RGB LED 控制系统

### 9.1 用户配置

- `brightness_percent`：全局 5–100%，默认 100%。
- `recording_color`：默认 `#FF3000`，网页和桌面端使用颜色选择器。
- 所有非零 RGB 通道统一按全局亮度缩放；5% 下限在 Web、USB、NVS 加载和驱动四层防御。
- 录制颜色不能全黑。

### 9.2 灯效定义

| 状态 | 灯效 |
| --- | --- |
| 开机/初始化 | 低亮中性白常亮 |
| 联网/对时 | 青色慢闪 |
| 网页热点等待 | 蓝色快闪，150 ms 亮/150 ms 灭 |
| 电脑已枚举、尚未握手 | 绿色缓慢呼吸，约 2.4 秒一个周期，至少 30 FPS 更新 |
| 桌面端 HELLO 成功 | 绿色常亮 |
| 正在录制 | 用户自定义录制颜色常亮 |
| 电量 <40% 且不低于低电量阈值 | 橙色快闪 3 次 |
| 电量低于低电量阈值 | 红色快闪 3 次，然后红色缓慢呼吸 5 秒 |
| 不可恢复错误 | 暗红色错误模式 |
| 休眠 | 熄灭 |

### 9.3 优先级

从高到低：

1. 低电量锁定提示；
2. 不可恢复错误；
3. 正在录制；
4. LED 临时预览；
5. USB 已握手；
6. USB 已枚举；
7. 网页热点/对时/开机；
8. 熄灭。

LED 控制器必须由单一任务写灯带。其他模块只提交状态或一次性 pattern，不直接调用底层 `led_strip_set_pixel()`。

## 10. USB 连接灯效

### 10.1 事件定义

- TinyUSB 完成主机枚举且 `tud_mounted()` 为真：进入 `usb_enumerated`，绿色慢呼吸。
- 收到并成功响应合法 `HELLO`：进入 `usb_handshake`，绿色常亮。
- 后续有效 `PING`/协议请求续租桌面连接状态。
- USB unmount/suspend、线缆断开、`END_SESSION` 或协议租约超时：清除 USB LED 状态，恢复底层启动/配网/空闲状态。

不要仅以 CDC DTR 为“电脑已识别”的唯一依据；Windows 枚举串口但尚未打开端口时也应显示绿色呼吸。DTR/line-state 可作为辅助诊断字段。

### 10.2 维护优先级

任何阶段一旦 HELLO 成功：

- 250 ms 内取消或退出 Wi-Fi/NTP/网页热点等待；
- 不启动自动录制；
- 保持串行维护并依赖 PING 续租；
- 连接灯保持绿色常亮，直到断开、结束会话或租约到期。

## 11. 开机状态机优化

### 11.1 初始化顺序

```text
RESET
  -> 判断 reset/wakeup 原因和低电量锁定
  -> NVS + 配置迁移
  -> board 最小初始化（生产版不扫全 I²C）
  -> LED 控制器
  -> 状态服务骨架
  -> USB 串行枚举
  -> 电池/RTC/IMU/TF 初始化
  -> 按启动类型分流
```

### 11.2 冷启动/手动上电

```text
先尝试从可信 RTC 恢复系统时间
  -> 若已保存 Wi-Fi：在总预算内连接并 NTP 对时
  -> 对时成功：写 RTC 和可信来源
  -> 对时失败：保留可信 RTC 或标记 untrusted
  -> 启动 Web + AP，蓝色快闪
  -> 10 秒内无 AP 客户端：关闭门户
  -> 有客户端：保持服务，客户端全部离开后 30 秒关闭
  -> 自动记录开启时录制一次
  -> 进入休眠
```

具体约束：

- 开机联网 + NTP 总预算建议 12 秒，不能再进行三轮各 15–25 秒的串行阻塞。
- 没有保存 SSID 时立即跳过 STA 连接。
- AP/Web/DNS 全部就绪后才开始 10,000 ms“无人连接”计时。
- 客户端在第 10 秒前连接即取消无人超时；保持连接期间不关闭。
- 最后一个客户端离开后保留 30 秒，门户总时长设置 5 分钟硬上限，防止长期耗电。
- USB HELLO 可在上述任意阶段抢占，取消耗时网络操作并进入维护。

### 11.3 自动唤醒

- 不启动 Wi-Fi、Web 或热点。
- 从可信 RTC 恢复系统时间。
- `auto_record_enabled=true` 时执行一次五秒记录。
- 记录完成或失败收尾后强制读取一次电池，再执行第 12 节策略。
- `auto_record_enabled=false` 时不再配置下一次 timer wake，避免空唤醒；摇晃唤醒按独立设置决定是否保留。

### 11.4 USB/MSC 恢复启动

- 保持 2.0.1 维护会话语义，不联网、不自动录制。
- 先建立最小配置和状态服务，再运行 CDC/MSC。
- `EXIT_MSC(next_mode="maintenance")` 回到串行维护，不能穿过普通冷启动门户和录制路径。

## 12. 自动唤醒后的电量策略

### 12.1 判定顺序

```text
自动唤醒 -> 录制/收尾 -> 等待 1 秒 -> 强制读取电量
  ├─ 读取失败：记录错误，不误判低电量，正常休眠
  ├─ SOC < low_battery_percent：红闪 3 次 + 红色慢呼吸 5 秒 -> 锁定休眠
  ├─ SOC < 40%：橙闪 3 次 -> 正常定时休眠
  └─ 其他：正常定时休眠
```

- 比较使用严格 `<`，低电量阈值分支优先于 40% 提醒。
- 0% 是合法测量值，不能再用 `soc > 0.1` 判断“是否有数据”；应使用 `available/last_error`。
- 电池提示只在自动唤醒录制后执行，避免用户在网页/USB 调试时被强制休眠。
- 若电压已低到无法安全写 TF 卡的硬件保护线，可在录制前执行紧急保护，但必须单独定义、记录原因，不能与用户低电量阈值混为一谈。

### 12.2 低电量锁定

- 使用 `RTC_NOINIT_ATTR` 保存带 magic/schema/CRC 的 `low_battery_latched`。
- 进入锁定休眠前先禁用全部 wake source，不配置 timer 和 IMU EXT1。
- 深睡唤醒、软件复位和看门狗复位不得清除锁定。
- 仅真实断电后的 `ESP_RST_POWERON` 清除锁定；连接电脑导致重新上电后仍可进入 USB 维护。
- 锁定模式不得循环唤醒、重复闪灯或反复尝试录制。

## 13. 网页端更新

新增“状态灯”区域：

- 全局亮度滑杆/数值：5–100%；
- 录制提示颜色选择器；
- “预览灯效”按钮；
- 独立保存按钮和明确错误提示。

同时完成：

- `/api/config` 改为 cJSON 严格解析，与 USB 共用配置校验服务。
- 低于 5% 的亮度在前端即时提示，后端仍必须再次拒绝。
- 保存录制分辨率后重新计算合法 FPS，不允许保存不匹配组合。
- `/api/status` 使用缓存服务，不启动音频/相机/IMU。
- 网页显示 Wi-Fi 连接状态、AP 客户数、IP、NTP 状态、RTC 有效性、当前时钟来源和最后校时来源。
- 开放热点页面不再返回明文保存密码；显示“密码已保存”，留空表示不修改，提供显式“清除密码”。若产品坚持网页显示明文，必须先增加门户认证，不能继续在开放 AP 上直接暴露。

## 14. 桌面端更新

桌面端通过能力协商新增“设备设置”区域：

- 连接成功后调用 `GET_STATUS` 和 `GET_CONFIG`。
- 录制画质：分辨率、JPEG 质量、帧率联动。
- Wi-Fi：保存的 SSID、密码编辑；密码默认遮罩并允许用户点击显示，且不持久化到电脑配置或日志。
- 时间：时区、NTP 服务器、RTC/当前时钟/最后校时来源只读状态。
- 系统：自动唤醒间隔、自动记录、低电量阈值。
- 状态灯：亮度 5–100%、录制颜色、即时预览。
- 保存使用 `expected_revision`，成功后重新读取确认；失败显示具体字段，不显示密码。
- 2.0.1 及旧固件不声明 `device_config_v1` 时隐藏或禁用这些控件，不影响原有同步流程。
- 手动调试页显示完整原始状态；一键工作流只显示用户有用的电量、时钟和连接摘要。

桌面端不得通过 MSC 修改 NVS 或配置文件；所有设备设置都通过 CDC 协议完成。

## 15. 其他修复和质量要求

1. `app_core.c` 不再通过读取状态启动音频；WebSocket 实时波形仍由显式实时预览功能负责。
2. 配置保存、Wi-Fi 任务、网页录制和 USB 设置命令增加互斥与 `BUSY` 状态。
3. USB 状态序列化改用 cJSON 或带严格长度检查的 builder，禁止 `snprintf` 截断后继续发送。
4. 所有任务创建失败都释放已分配队列/句柄；状态服务和 LED 服务增加栈水位监测。
5. 生产构建关闭全 I²C 扫描和高频调试日志，保留按模块的错误日志。
6. RTC ISO8601 必须携带时区偏移；无效时间返回 JSON null。
7. Wi-Fi SSID、密码、NTP、时区和颜色字段完成 JSON 转义与 UTF-8 长度测试。
8. 配置和状态结构增加 schema/version，未来字段只能追加。
9. 记录 metadata 增加本次时钟来源与可信度，但保持 schema 2 兼容追加。
10. 任何低电量、USB 或配置新逻辑都不得绕过录制目录的原子 finalize 规则。

## 16. 测试矩阵

### 16.1 单元和契约测试

- NVS 2.0.1 配置迁移到 2.1.0，所有旧字段保持。
- 亮度 4/5/100/101、全黑颜色、非法颜色格式。
- 每个分辨率与 FPS 上限组合、JPEG 质量边界。
- SSID/密码/NTP/TZ 最大长度、Unicode、引号、反斜杠和控制字符。
- SET_CONFIG 原子失败、revision 冲突、录制中 BUSY。
- GET_STATUS 所有 available/error 组合和最坏长度 `<1600`。
- RTC 低压位、非法 BCD、从未可信校时、NTP 写入后、RTC 恢复后。
- LED 优先级、临时预览恢复、快闪次数和呼吸时长。
- 低电量 39.9%、恰好 40%、恰好阈值、低于阈值、读取失败。
- 低电量锁定跨 deep sleep/soft reset 保留，只在 power-on 清除。
- 10 秒无 AP 客户、9.5 秒连接、连接后退出、门户硬上限。
- USB 枚举、HELLO、PING 租约、unmount 和 END_SESSION 状态转换。

### 16.2 回归测试

- 当前 40 项固件契约测试和 16 个子测试全部通过。
- ESP-IDF clean build，无新增 warning。
- v1/2.0.0/2.0.1 桌面端协议兼容测试。
- 自动发现、RO/RW MSC、安全弹出、事务导出/删除和断电恢复。
- 五秒视频/音频/IMU 同步录制、metadata schema 2 和原子目录提交。
- Web 预览、手动录制、Wi-Fi 扫描/配网、NTP 和配置保存。

### 16.3 实机验收

1. 冷启动无保存 Wi-Fi：热点可见，10 秒无人连接后进入录制。
2. 冷启动保存 Wi-Fi：在总预算内对时；失败时从可信 RTC 恢复，不无限等待。
3. 热点第 9.5 秒连接：网页不会被提前关闭；离开 30 秒后关闭。
4. 插入电脑但不打开桌面端：绿色平滑慢呼吸。
5. 桌面端 HELLO 成功：绿色常亮，版本/序列号/状态/配置可读。
6. 拔线或结束会话：USB 灯效清除并恢复正确状态。
7. 设置 5%、35%、100% 亮度，所有模式都遵循全局亮度；不能保存 4%。
8. 自定义录制颜色后下一次录制显示正确，重启后仍保留。
9. 模拟 39%、阈值以上：录制后橙闪三次并正常定时休眠。
10. 模拟低于阈值：录制后红闪三次、红色呼吸五秒，随后不再定时唤醒。
11. 软件复位不清低电量锁定；真实断电重启清除。
12. 连续 30 分钟 USB 状态轮询无堆泄漏、看门狗或录制干扰。

## 17. 建议里程碑与提交顺序

### 固件分支

1. `refactor: add unified settings and cached status services`
2. `feat: expose device status and settings over usb`
3. `feat: add configurable prioritized rgb led controller`
4. `feat: optimize boot time sync and portal state machine`
5. `feat: add post-record battery alerts and sleep lockout`
6. `fix: harden rtc trust config parsing and startup cleanup`
7. `test: cover firmware 2.1 settings status led and power flows`
8. `docs: document firmware 2.1 protocol and user-visible behavior`

每个提交必须独立构建并运行对应测试。涉及 NVS、USB、录制或休眠的提交必须保留可回退点。

### 桌面端分支

1. `feat: add firmware 2.1 status and config protocol support`
2. `feat: add connected device settings and led preview ui`
3. `test: cover capability gating config validation and secret handling`
4. `build: publish firmware 2.1 compatible windows package`

## 18. 发布验收门槛

- 固件版本报告 `2.1.0`，并声明新的 capability。
- 冷启动、自动唤醒、USB 维护、MSC 恢复和低电量锁定五条状态机均有日志和测试证据。
- 首次 HELLO 和 GET_STATUS 响应时间均小于 100 ms。
- 无客户端时网页热点严格在 AP 就绪后约 10 秒退出。
- 所有配置可从 Web/USB 保存、重启后读取一致。
- RGB 亮度任何入口均不能低于 5%。
- 低电量锁定后没有定时或摇晃唤醒。
- 不再产生未经可信校时的 2070 日期记录。
- 旧桌面端和旧记录格式完全兼容。
- 实机完成至少 50 次自动唤醒录制循环、10 次 MSC 切换和一次低电量完整流程。

## 19. 后续对话执行提示词

> 请在 `D:\ESP-IDF\Projects\Day_Distiller_HW2.0_V1` 的 `codex/firmware-v2` 分支继续实现 Day Distiller 固件 2.1.0。开始前完整阅读 `docs/firmware_v2_1_implementation_plan_zh.md`、`docs/usb_link_protocol.md`、`docs/firmware_v2_implementation_plan_zh.md`，并以当前 `0dbce23` 之后的最新提交为基线。严格保持 USB 帧版本 1、命令 1–11、VID/PID、录制文件名、metadata 和事务导出兼容。按计划先重构统一配置与缓存状态服务，再实现 USB 配置命令和状态字段，然后实现单任务 RGB 控制器、启动/门户状态机和低电量锁定。每个里程碑必须运行固件测试、ESP-IDF clean build，并在实机验证后独立提交。不得把 Wi-Fi 密码写入日志或普通状态响应，不得让 GET_STATUS 启动任何传感器，不得因任何失败删除设备记录。固件完成后，再到桌面端分支实现 capability-gated 的设备设置和 LED 预览 UI。
