# Day Distiller 固件 2.1：完整设备状态接口更新计划

文档状态：后续版本计划
基线固件：2.0.0
目标版本：2.1.0
兼容要求：USB 帧协议继续保持版本 1，`GET_STATUS` 命令 ID 继续为 3

## 1. 背景和结论

固件 2.0 已经在设备内部采集电池、RTC、IMU、存储、摄像头、音频和 Wi-Fi 状态，Web 管理页面也能读取其中大部分信息。但是 USB Link 的 `HELLO`、`PING` 和 `GET_STATUS` 当前只返回：

- 固件版本、设备 ID和运行模式；
- USB 维护会话和录制状态；
- TF 卡容量、挂载和 MSC 暴露状态；
- 元数据版本、活动导出数量和能力列表。

2.0 的 USB 状态响应没有包含电量、充电状态、RTC 有效性、RTC 时间、IMU、摄像头和音频健康信息。桌面端无法通过 USB 判断这些状态，因此需要在固件 2.1 中补齐。

## 2. 2.1 目标

1. 保持现有 `GET_STATUS {}` 请求完全兼容，只向响应追加字段。
2. 桌面端连接设备后可以读取电量、充电估计、RTC 时间和各采集模块健康状态。
3. 状态读取必须非阻塞，不得因为读取电量或传感器导致 HELLO 超时。
4. 状态读取不得启动麦克风、摄像头预览、Wi-Fi 门户或新的录制任务。
5. 串行维护和 MSC 维护两种模式都返回结构稳定的 JSON；模块不可用时返回 `available:false` 和错误原因，而不是省略字段或伪造零值。
6. 完整响应必须始终小于 USB Link 1600 字节 JSON 上限。

## 3. 建议状态结构

在现有状态 JSON 中追加：

```json
{
  "battery": {
    "available": true,
    "soc_percent": 76.4,
    "voltage_v": 3.91,
    "charge_state": "estimated",
    "confidence": 80,
    "sample_age_ms": 420,
    "last_error": 0
  },
  "rtc": {
    "available": true,
    "valid": true,
    "unix_time": 1784275200,
    "iso8601": "2026-07-17T13:20:00+08:00",
    "timezone": "CST-8",
    "sample_age_ms": 310,
    "last_error": 0
  },
  "sensors": {
    "imu": {
      "available": true,
      "configured_rate_hz": 104,
      "last_error": 0
    },
    "camera": {
      "available": true,
      "initialized": false,
      "last_error": 0
    },
    "audio": {
      "available": true,
      "initialized": false,
      "sample_rate_hz": 48000,
      "last_error": 0
    }
  },
  "power": {
    "wake_reason": "usb_or_reset",
    "reset_reason": "software_reset",
    "deep_sleep_allowed": false
  }
}
```

字段要求：

- `available` 表示该模块本次启动是否完成了最小初始化。
- `last_error` 使用稳定的整数错误码；桌面端可以同时显示本地化说明。
- `sample_age_ms` 说明缓存数据的新鲜程度。
- `soc_percent` 和 `voltage_v` 使用 JSON 数值，不使用带单位字符串。
- `charge_state` 固定为 `unknown`、`not_charging` 或 `estimated`。
- RTC 无效时 `valid:false`，`unix_time` 和 `iso8601` 使用 `null`，不得返回 1970 年伪时间。
- MSC 模式无法读取的模块仍保留相同对象结构，并返回 `available:false`。

## 4. 实现架构

建议新增独立 `device_status_service.c/.h`，由应用核心维护缓存快照，USB 协议层只序列化快照：

```text
battery / rtc / imu / camera / audio drivers
                 │
                 ▼
        device_status_service
        - 周期或按需刷新
        - 缓存时间戳
        - 初始化状态
        - 稳定错误码
                 │
                 ▼
          usb_link GET_STATUS
```

禁止让 `usb_link.c` 为响应一个请求而直接启动音频、摄像头或 Wi-Fi。`make_status_payload()` 只读取已有缓存，目标响应时间不超过 100ms。

## 5. 启动与模式要求

### 串行模式

- USB 可以继续早于完整传感器初始化启动。
- 初始化尚未完成时先响应 `available:false`，不能让 HELLO 等待数秒。
- 电池和 RTC 初始化完成后，下一次 GET_STATUS 自动返回有效数据。

### MSC 模式

- 进入 MSC 的快速启动路径目前跳过大部分应用初始化。
- 2.1 应只初始化读取电池和 RTC 所需的最小 I2C/ADC 资源，前提是不会与 TF 卡 SPI 和 TinyUSB 冲突。
- 如果硬件资源或功耗不允许，返回 `available:false`，但 JSON schema 必须保持一致。

### 录制模式

- `GET_STATUS` 不得干扰三路采集。
- 录制期间返回最近缓存值，并通过 `sample_age_ms` 表明数据可能较旧。

## 6. USB 能力声明

固件 2.1 在 capabilities 中追加：

```json
[
  "device_status_v2",
  "battery_status",
  "rtc_status",
  "sensor_health"
]
```

桌面端必须以 capability 判断字段是否受正式支持，不能只依赖 `firmware_version >= 2.1.0`。

## 7. 桌面端兼容要求

当前桌面端已经按以下字段做好兼容：

- `battery.soc_percent`，同时兼容旧命名 `battery.soc`；
- `battery.voltage_v`，同时兼容旧命名 `battery.voltage`；
- `rtc.valid`；
- `rtc.iso8601`，同时兼容旧命名 `rtc.iso`。

固件 2.1 应优先使用正式字段 `soc_percent`、`voltage_v` 和 `iso8601`。旧固件不提供这些对象时，桌面端显示“当前固件未通过 USB 提供”。

## 8. 测试和验收

- 2.0 桌面端连接 2.1 固件时仍能完成 HELLO、同步和事务删除。
- 2.1 GET_STATUS 响应长度在所有状态组合下小于 1600 字节。
- 冷启动后 100ms 内的首次 HELLO 不因传感器未初始化而超时。
- 电量和 RTC 初始化完成后，连续读取状态字段稳定且 `sample_age_ms` 合理。
- RTC 拔除、I2C 故障和电池 ADC 故障均返回 `available/valid/last_error`，设备不崩溃。
- 录制期间每秒 GET_STATUS 不增加明显丢帧、音频缺口或 IMU 采样抖动。
- MSC 模式返回相同 schema，安全弹出和退出维护行为不受影响。
- 维护模式连续读取 30 分钟无堆增长和任务看门狗复位。

## 9. 建议提交顺序

1. `feat: add cached device status service`
2. `feat: expose battery rtc and sensor health over usb`
3. `test: validate non-blocking device status in serial and msc modes`
4. `docs: document firmware 2.1 usb device status schema`
