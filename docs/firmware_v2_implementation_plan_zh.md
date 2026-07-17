# Day Distiller 设备固件 2.0 更新计划书

文档状态：可执行交接稿  
目标分支：`codex/firmware-v2`  
基线提交：`b598da5`  
目标硬件：Day Distiller HW 2.0 / ESP32-S3  
目标固件版本：`2.0.0`  
USB 帧协议版本：继续保持 `1`

## 1. 文档目的

本文用于把固件 2.0 的范围、不可破坏的兼容性约束、状态机、协议、记录格式、异常恢复、测试标准和实施顺序一次性冻结。后续开发对话应以本文为主任务说明，在当前 `codex/firmware-v2` 分支继续实施，不应重新设计一套与桌面端不兼容的协议。

固件 2.0 的核心目标不是在设备端运行 AI，而是让设备成为一个可靠、可恢复、可验证的数据采集与导出端：

1. 保持当前五秒视频、音频、IMU 定时记录和深睡行为可用。
2. 为每次记录提供准确、可对齐、可追溯的元数据。
3. 允许桌面端安全地同步一天或多天的记录。
4. 只有在桌面端确认日报邮件已经被服务器接受后，才允许设备删除对应原始记录。
5. USB 断开、程序崩溃或设备断电时，未提交的数据必须保留；正在进行的删除必须可以幂等恢复。
6. 当前桌面应用和旧版主机仍可继续使用协议 v1 的 MSC 流程。

## 2. 2.0 发布范围

### 2.1 必须完成（P0）

- 协议 v1 命令 `HELLO`、`PING`、`GET_STATUS`、`ENTER_MSC`、`EXIT_MSC` 行为保持兼容。
- 增加严格的 JSON 参数解析，替换当前基于 `strstr` 的参数判断。
- 增加可跨重启保持的 USB 维护会话和 `EXIT_MSC(next_mode="maintenance")`。
- 增加按日期冻结记录集、生成导出清单、查询状态、提交删除、放弃导出和结束会话的事务化导出能力。
- 增加多日期发现和“一次挂载、同步多天、按天独立提交删除”的支持。
- 增加元数据 schema v2、三路采集同步偏移和实际采集时长。
- 记录目录原子化完成；未写完的记录不能被桌面端当作有效记录导出。
- 删除事务具备断电恢复、重复提交幂等和严格路径白名单。
- 完成旧主机/新主机与旧固件/新固件的兼容矩阵测试。

### 2.2 应完成（P1）

- 使用芯片唯一标识生成稳定的 `device_id` 和 USB 序列号。
- 音频、IMU 改为边采集边写入临时文件，降低 PSRAM 占用和断电损失面。
- 增加 USB 同步、提交删除、等待主机和错误状态的 LED 表现。
- 增加导出事务自动清理策略，只清理已提交或已放弃的旧事务文件，不清理原始记录。
- 为协议解析、清单、路径校验、状态机和删除恢复增加设备侧单元测试。

### 2.3 暂不纳入 2.0

- 设备端 AI、OCR、语音识别、IMU 分类模型和日报生成。
- 人脸识别、地点记忆或任何用户画像逻辑。
- 云端模型、邮件、Resend 和 API Key 管理。
- OTA 更新、记录加密、USB 身份认证和手机端同步；这些可以在 2.1 以后单独设计。
- 改变视频、音频基础兼容格式。2.0 仍输出 `video.avi`、`audio.wav`、`imu.json` 和 `meta.json`。

## 3. 不可破坏的设计约束

| 项目 | 冻结要求 |
| --- | --- |
| USB 帧版本 | 继续为 `1`，不得因为固件版本为 2.0 而改成 `2`。 |
| 帧格式 | 保留 SLIP、20 字节头、CRC32、2048 字节最大帧和 1600 字节 JSON 上限。 |
| 旧命令 | 命令 ID 1–5、状态码 0–8 和现有请求字段继续有效。 |
| USB 描述符 | 保留 VID `0x303A`、串行 PID `0x4020`、MSC PID `0x4021` 和接口布局。 |
| 文件兼容 | 保留 `REC_XXXX_YYMMDD_HHMMSS` 目录名以及四个现有文件名。 |
| MSC 导入 | 正常同步必须使用只读 MSC；读写 MSC 只为旧版主机兼容保留。 |
| 删除时机 | 未收到明确的 `COMMIT_EXPORT_DELETE` 时绝不删除记录。 |
| 删除范围 | 只能删除导出清单中被冻结的目录，不能根据日期重新扫描后批量删除。 |
| 失败处理 | 导入、云端生成、邮件发送或 USB 退出任一失败都不得触发删除。 |
| 多日期 | 每个日期拥有独立导出事务和独立提交结果；一天失败不能影响其他日期。 |
| 当前桌面端 | 即使检测到 2.0 固件，也必须能继续走现有 `legacy_msc_v1` 路径。 |

能力命名需要兼容已经存在的不一致：固件应同时公布 `transactional_export_v2` 和 `export_transactions`。其中 `transactional_export_v2` 是后续桌面端选择适配器时使用的正式名称，`export_transactions` 仅作为早期草案兼容别名。

## 4. 当前固件基线与需要解决的问题

当前代码已经具备双 CDC 串行模式、CDC+MSC 维护模式、协议 v1、只读/读写 MSC、五秒三路并行采集、RTC、TF 卡和深睡基础能力。固件 2.0 应在此基础上增量修改，不应推倒重写摄像头、音频、IMU 和网页配置模块。

当前实现存在以下与生产使用直接相关的缺口：

1. MSC 模式只通过 RTC 内存中的简单 magic 标记切换，无法表达“退出 MSC 后继续维护而不是录制”的意图。
2. `EXIT_MSC` 后回到普通启动路径，存在 Wi-Fi 门户、自动录制和维护会话之间的竞争。
3. JSON 参数使用字符串搜索，可能错误识别字段值，也无法严格报告非法参数。
4. 没有固件侧不可变导出清单，删除安全完全依赖主机重新比对目录。
5. 没有导出事务日志，断电后无法判断删除是否已经开始或完成。
6. 记录目录在采集开始前就以正式 `REC_...` 名称出现，掉电时可能留下被误认为有效记录的半成品。
7. 当前 `meta.json` 的 `duration_s` 是固定配置值，`start_us` 是本次启动后的单调时间，不能用于跨记录的真实时间线。
8. 摄像头任务使用固定五秒推算 AVI 帧间隔，音频和 IMU 没有统一记录首样本相对偏移。
9. 记录序号扫描后对 10000 取模，长期使用后可能与尚未删除的目录碰撞。
10. 当前 USB 字符串序列号为固定 `DD-USB-LINK`，多台设备无法形成稳定且唯一的身份。

## 5. 固件总体状态机

2.0 不应继续依赖零散布尔值表达 USB、录制和导出状态。建议引入统一运行状态：

```text
NORMAL_BOOT
  ├─ 自动/手动唤醒 ─> RECORD_PREPARING ─> RECORDING ─> SLEEP_PENDING
  └─ 收到 HELLO/PING ─> SERIAL_MAINTENANCE

SERIAL_MAINTENANCE
  ├─ BEGIN_EXPORT ─> EXPORT_PREPARING ─> SERIAL_MAINTENANCE
  ├─ ENTER_MSC(ro) ─> REBOOT_TO_MSC ─> MSC_READ_ONLY
  ├─ COMMIT_EXPORT_DELETE ─> COMMITTING_DELETE ─> SERIAL_MAINTENANCE
  └─ END_SESSION / 超时 ─> SLEEP_PENDING

MSC_READ_ONLY
  ├─ 主机安全弹出 + EXIT_MSC(next_mode=maintenance)
  │    └─ REBOOT_TO_SERIAL_MAINTENANCE ─> SERIAL_MAINTENANCE
  └─ 异常断开/超时 ─> 保留事务和记录，回到安全启动路径
```

强制规则：

- `RECORDING`、`EXPORT_PREPARING`、`MSC_READ_ONLY`、`COMMITTING_DELETE` 互斥。
- 进入维护会话后禁止自动录制，直到 `END_SESSION` 或维护超时。
- MSC 暴露 TF 卡之前必须卸载设备侧 FAT 文件系统；退出 MSC 且主机完成弹出之后，才能重新由设备挂载。
- 设备侧正在挂载 TF 卡时不得同时向主机暴露同一块卡。
- 维护超时只结束会话，不得自动提交或删除任何导出事务。

## 6. 跨重启维护意图

将当前 `day_usb_boot_flag_t` 扩展为带版本和校验的 RTC 启动意图，例如：

```c
typedef struct {
    uint32_t magic;
    uint16_t schema_version;
    uint8_t target_mode;      // normal / msc / serial_maintenance
    uint8_t msc_access;       // ro / rw
    uint64_t session_id;
    char active_export_id[40];
    uint32_t crc32;
} day_usb_boot_intent_t;
```

要求：

- 所有字段均参与 CRC，magic、版本、枚举范围和 CRC 全部有效才接受。
- `ENTER_MSC` 在回复成功前先写入 `target_mode=msc`，刷新 CDC 回复后再重启。
- `EXIT_MSC(next_mode="maintenance")` 在回复成功前写入 `target_mode=serial_maintenance`，不得清成普通启动。
- 普通 `EXIT_MSC` 请求保持 v1 语义，仍可回到正常启动。
- 冷掉电导致 RTC 内存丢失时，TF 卡上的导出事务仍然存在；设备按普通安全启动，不做删除。
- `END_SESSION` 清除维护意图，并让应用进入既有深睡路径。

## 7. USB Link 2.0 增量协议

### 7.1 状态响应新增字段

保留所有现有字段，并追加：

```json
{
  "protocol": 1,
  "firmware_version": "2.0.0",
  "device_id": "DD-A1B2C3D4E5F6",
  "mode": "serial",
  "runtime_state": "serial_maintenance",
  "session_id": "8f31c8d4d6be4d54",
  "recording": false,
  "metadata_schemas": [1, 2],
  "active_exports": 2,
  "capabilities": [
    "enter_msc",
    "exit_msc",
    "msc_rw",
    "msc_ro",
    "slip_crc32_json",
    "transactional_export_v2",
    "export_transactions",
    "export_manifest_v2",
    "list_record_dates",
    "exit_to_maintenance",
    "commit_export_delete",
    "abort_export",
    "get_export_status",
    "end_session"
  ]
}
```

状态 JSON 超过上限时不得截断形成非法 JSON。应减少非必要字段或返回明确内部错误。

### 7.2 命令 ID

| ID | 命令 | 模式 | 说明 |
| ---: | --- | --- | --- |
| 1–5 | 现有命令 | 保持现状 | 不改变原语义。 |
| 6 | `BEGIN_EXPORT` | serial maintenance | 冻结某个本地日期的完整记录集并生成清单。 |
| 7 | `COMMIT_EXPORT_DELETE` | serial maintenance | 按清单幂等删除已成功交付的记录。 |
| 8 | `ABORT_EXPORT` | serial maintenance | 放弃事务但保留全部记录。 |
| 9 | `END_SESSION` | serial maintenance | 结束维护并允许设备深睡。 |
| 10 | `GET_EXPORT_STATUS` | serial maintenance | 查询一个事务，或分页查询未完成事务。 |
| 11 | `LIST_RECORD_DATES` | serial maintenance | 分页返回设备中完整记录的日期、数量和大小。 |

### 7.3 LIST_RECORD_DATES

请求：

```json
{"cursor":0,"limit":20}
```

响应：

```json
{
  "items":[
    {"date":"2026-07-17","record_count":144,"total_bytes":734003200},
    {"date":"2026-07-16","record_count":132,"total_bytes":681574400}
  ],
  "next_cursor":null
}
```

只统计已经原子完成的正式 `REC_...` 目录。分页用于保证响应永远小于 1600 字节。

### 7.4 BEGIN_EXPORT

请求：

```json
{
  "date":"2026-07-17",
  "client_request_id":"desktop-01K0A1F2M8D7"
}
```

响应：

```json
{
  "export_id":"exp-DD-A1B2C3D4E5F6-019f6403",
  "date":"2026-07-17",
  "state":"prepared",
  "record_count":144,
  "total_bytes":734003200,
  "manifest_path":"/EXPORTS/exp-DD-A1B2C3D4E5F6-019f6403.json",
  "manifest_sha256":"64位十六进制摘要"
}
```

要求：

- 日期严格使用设备当前时区下的 `YYYY-MM-DD`，非法日期返回 `INVALID_ARG`。
- `client_request_id` 用作幂等键。同一个请求重试必须返回同一个 `export_id`，不能创建重复事务。
- 一个日期允许存在一个 `prepared` 事务；设备应允许多个日期同时处于 `prepared`，从而一次 MSC 挂载同步多天。
- 建议最多保留 32 个未完成事务；超过限制返回 `BUSY` 并提示主机提交或放弃旧事务。
- `BEGIN_EXPORT` 与录制、MSC、删除互斥。
- 冻结之后不允许修改清单中目录；新产生的记录不会自动加入旧事务。
- 空日期返回成功的空清单还是错误必须固定。2.0 采用 `INVALID_ARG`，响应说明 `no complete records for date`。

### 7.5 ENTER_MSC 扩展

现有请求继续有效：

```json
{"access":"ro"}
```

2.0 主机可以附带本次准备的事务列表：

```json
{
  "access":"ro",
  "export_ids":["exp-...-01","exp-...-02"]
}
```

`export_ids` 仅用于状态关联和校验，MSC 仍暴露整块 FAT 卷，不能假装实现文件级隔离。正式 2.0 工作流必须使用 `ro`。如果事务不存在或不是 `prepared`，返回 `INVALID_ARG`，不得重启进入 MSC。

### 7.6 EXIT_MSC 扩展

旧请求保持有效：

```json
{"force":false}
```

正式 2.0 请求：

```json
{"force":false,"next_mode":"maintenance"}
```

- 未检测到主机安全弹出且 `force=false` 时返回 `BAD_STATE`。
- `next_mode` 只接受 `normal` 或 `maintenance`。
- `maintenance` 必须跨重启保留当前会话，跳过 Wi-Fi 门户、自动录制和深睡，立即恢复协议串口。
- `force=true` 只用于人工故障恢复，桌面端正常流程不得使用。

### 7.7 GET_EXPORT_STATUS

请求单个事务：

```json
{"export_id":"exp-..."}
```

响应包含 `prepared`、`committing`、`committed` 或 `aborted`，以及总数、已删除数、最后错误和清单摘要。请求不带 `export_id` 时使用 `cursor`/`limit` 分页返回未完成事务，供桌面端崩溃后恢复。

### 7.8 COMMIT_EXPORT_DELETE

请求：

```json
{
  "export_id":"exp-...",
  "manifest_sha256":"64位十六进制摘要",
  "confirm_record_count":144
}
```

要求：

- 只能在串行维护模式且 TF 卡由设备侧挂载时执行。
- 固件必须同时匹配 `export_id`、清单 SHA-256 和记录数量，任一不匹配都拒绝删除。
- 先将事务状态原子写为 `committing`，再逐目录删除。
- 每成功删除一个目录后持久化进度，或采用可从“目录已不存在”安全恢复的幂等算法。
- 重复提交 `committed` 事务返回成功和同一最终结果。
- `committing` 状态在掉电重启后不自动开始删除；只有主机再次提交同一事务，或明确进入受控恢复流程后才继续。
- 任何路径异常、目录内容发生不可接受变化或存储错误都停止操作，保留剩余数据并返回 `STORAGE_ERROR`。
- 提交一个日期不得删除其他日期或其他事务的记录。

### 7.9 ABORT_EXPORT

请求：

```json
{"export_id":"exp-..."}
```

`prepared` 可以转为 `aborted`；`aborted` 重复请求返回成功；`committed` 返回当前已提交状态；`committing` 不允许直接放弃，应先完成故障恢复。放弃事务只清理事务元数据，永远不删除 `REC_...` 目录。

### 7.10 END_SESSION

请求为 `{}`。设备先发送成功响应并刷新 CDC，再清除维护启动意图。未完成导出事务继续持久化，设备随后进入现有深睡流程。结束会话不得隐式提交、放弃或删除事务。

## 8. 导出清单和事务持久化

建议新增 `export_service.c/.h`，所有事务文件位于：

```text
/sdcard/EXPORTS/
  exp-<device>-<id>.json
  exp-<device>-<id>.state
```

清单示例：

```json
{
  "schema_version":2,
  "export_id":"exp-DD-A1B2C3D4E5F6-019f6403",
  "client_request_id":"desktop-01K0A1F2M8D7",
  "device_id":"DD-A1B2C3D4E5F6",
  "date":"2026-07-17",
  "created_at_utc_ms":1784259600123,
  "state":"prepared",
  "record_count":2,
  "total_bytes":10485760,
  "records":[
    {
      "name":"REC_0042_260717_110000",
      "record_id":"DD-A1B2C3D4E5F6-1784266800000-0042",
      "files":[
        {"name":"meta.json","size":1850},
        {"name":"video.avi","size":7340032},
        {"name":"audio.wav","size":480044},
        {"name":"imu.json","size":524288}
      ]
    }
  ]
}
```

实现要求：

- 只允许清单中出现纯目录名和纯文件名，禁止 `/`、`\\`、`..`、驱动器前缀和控制字符。
- 目录名必须重新通过严格的 `REC_XXXX_YYMMDD_HHMMSS` 校验。
- 清单先写 `.tmp`，`fflush`、`fsync` 后 `rename`，不得原地覆盖唯一有效副本。
- 状态更新采用同样的临时文件+原子替换策略。
- 固件只需对较小的清单计算 SHA-256。媒体文件 SHA-256 继续由电脑在复制时计算，避免设备长时间高负载读取所有媒体。
- 删除前重新读取清单并验证摘要，不能信任 RAM 中的旧结构。
- 已提交/放弃事务保留至少 7 天用于诊断，再由独立 housekeeping 清理；housekeeping 不得触碰记录目录。

## 9. 多日期同步的正式工作流

一次连接中允许发现并同步多天：

1. 桌面端每秒发现设备，发送 `HELLO` 和 `GET_STATUS`。
2. 桌面端分页调用 `LIST_RECORD_DATES`。
3. 对需要同步的每个日期分别调用 `BEGIN_EXPORT`，获得多个独立 `export_id`。
4. 调用一次 `ENTER_MSC(access="ro", export_ids=[...])`。
5. Windows 挂载卷后，桌面端读取各事务清单，与本地缓存比较，只复制新增或变化文件。
6. 桌面端逐文件校验大小和 SHA-256，然后安全弹出卷。
7. 调用 `EXIT_MSC(next_mode="maintenance")`，设备回到串行维护模式。
8. 用户选择一个、多个或全部日期蒸馏。未选择日期可以 `ABORT_EXPORT`，原始数据保留。
9. 云端处理期间桌面端每 30 秒 `PING`。
10. 某一天的邮件被 SMTP 服务器接受后，只提交该日期的 `export_id`。
11. 多天任务逐日独立提交；失败日期保留记录并可重试。
12. 全部处理结束后调用 `END_SESSION`。

在桌面端尚未实现上述 2.0 适配器之前，新固件必须继续允许其扫描整块只读 MSC、复制记录、退出 MSC，并按旧版读写挂载流程进行主机侧精确清理。

## 10. 记录元数据 schema v2

### 10.1 原子记录目录

新记录先写入隐藏或非 `REC_` 前缀临时目录，例如：

```text
/sdcard/.recording/REC_0042_260717_110000.partial/
```

完成流程：

1. 创建临时目录。
2. 三路采集写各自临时文件。
3. 关闭、刷新并同步所有媒体文件。
4. 最后写入 `meta.json`，其中 `record_state="complete"`。
5. 同步目录并原子重命名为正式 `/sdcard/REC_0042_260717_110000`。

启动时扫描 `.recording`：半成品默认保留并写诊断日志，不进入导出日期列表。可以提供人工恢复工具，但 2.0 不自动把半成品伪装成完整记录。

### 10.2 统一采集时基

当前固定延迟后同时设置 START 位的方式应升级为 READY/START 屏障：

1. camera、audio、IMU 任务分别完成文件打开和硬件准备后设置 READY 位。
2. 协调器等待三路 READY，超时的流记录错误但不永久阻塞其他流。
3. 协调器在发出 START 前记录统一 `capture_epoch_us=esp_timer_get_time()`。
4. 每一路记录真实的首帧/首样本和末帧/末样本时间，相对 `capture_epoch_us` 输出偏移。
5. `actual_duration_ms` 使用真实停止时间计算，不再固定写成五秒。

### 10.3 meta.json 建议结构

```json
{
  "schema_version":2,
  "record_state":"complete",
  "record_id":"DD-A1B2C3D4E5F6-1784266800000-0042",
  "device_id":"DD-A1B2C3D4E5F6",
  "sequence":42,
  "wake_reason":"timer",
  "start_time_utc_ms":1784266800000,
  "local_time":"2026-07-17T11:00:00+08:00",
  "timezone":"CST-8",
  "time_quality":"rtc_valid",
  "capture_epoch_monotonic_us":12800421,
  "actual_duration_ms":5038,
  "streams":{
    "video":{
      "file":"video.avi",
      "container":"avi",
      "codec":"mjpeg",
      "width":640,
      "height":480,
      "target_fps":10,
      "actual_fps":9.72,
      "frame_count":49,
      "first_frame_offset_us":18342,
      "last_frame_offset_us":4982431,
      "error":"ESP_OK"
    },
    "audio":{
      "file":"audio.wav",
      "sample_rate_hz":48000,
      "channels":1,
      "sample_format":"pcm_s16le",
      "sample_count":240384,
      "first_sample_offset_us":6210,
      "last_sample_offset_us":5014210,
      "error":"ESP_OK"
    },
    "imu":{
      "file":"imu.json",
      "sample_rate_hz":104,
      "sample_count":521,
      "first_sample_offset_us":2940,
      "last_sample_offset_us":5011023,
      "timestamp_unit":"us",
      "error":"ESP_OK"
    }
  }
}
```

兼容要求：

- 桌面端仍可从目录名解析日期；schema v2 是增强而不是替代目录名。
- `imu.json` 中每个 `t_us` 必须相对统一采集 epoch，而不是相对任务创建时间。
- 缺失或失败的流仍应在 `streams` 中写明错误，不得伪造零偏移为真实时间。
- RTC 无效时写 `time_quality="invalid"`，不得伪装成可靠 UTC；目录名可使用受控回退时间，但必须在元数据中说明。
- 可新增 `timing.json` 保存逐视频帧时间戳；如果 2.0 首版不实现逐帧索引，至少必须提供首帧、末帧和实际帧率。

## 11. 记录可靠性改造

- 修复序号碰撞：使用 NVS 单调计数器，并在创建目录前探测冲突；不能简单 `% 10000` 后直接失败。目录中的四位序号可以循环，但 `record_id` 必须全局唯一。
- 音频建议直接写入临时 WAV，结束时补写头部，不再将整段音频全部留在 PSRAM 后一次性写卡。
- IMU 可以保持最终 JSON 格式，但应流式写入临时文件并在结束时闭合 JSON，避免大数组常驻内存。
- 摄像头 AVI 必须使用实际采集间隔完成索引，不使用固定 `DAY_RECORD_SECONDS` 推算。
- 所有 `fwrite`、`fflush`、`fsync`、`fclose` 和 `rename` 结果必须检查。
- TF 空间不足时应在录制前拒绝开始，写明确错误状态并进入低功耗路径。
- 三路某一路失败时，其他成功流可以保留，但元数据必须标记 `partial_stream_failure`；是否允许导出由 `record_state` 与策略明确决定。建议只要 `meta.json` 完整写出就允许导出，同时由桌面端降级处理。

## 12. 设备身份和 USB 枚举

- `device_id` 建议由 ESP32-S3 eFuse base MAC 派生为 `DD-XXXXXXXXXXXX`，不包含用户信息，也不是秘密。
- `record_id`、导出 ID、清单和状态响应都使用同一 `device_id`。
- USB iSerialNumber 建议改为稳定的设备唯一值，解决多台设备 COM 口身份冲突。
- 修改 USB 序列号后 Windows 可能为现有样机分配一次新的 COM 口，这是预期的一次性迁移；VID、PID 和接口布局不得变化。
- 桌面端仍应通过 HELLO 确认设备身份，不能只依赖 COM 号。

## 13. LED 和用户可感知行为

建议在现有 `day_led_mode_t` 中增加：

| 状态 | 建议表现 |
| --- | --- |
| `DAY_LED_USB_WAITING` | 低亮蓝色呼吸，表示维护会话已建立。 |
| `DAY_LED_USB_MSC` | 稳定蓝色，表示电脑正在读取 TF 卡。 |
| `DAY_LED_EXPORT_COMMIT` | 短周期蓝紫脉冲，表示正在执行删除提交。 |
| `DAY_LED_EXPORT_DONE` | 绿色短亮后回到等待状态。 |
| 存储/事务错误 | 保留现有红色错误模式。 |

LED 只用于反馈，不能作为状态机真相来源。MSC 读写期间不要使用会明显抢占 SPI、CPU 或中断的复杂动画。

## 14. 代码结构建议

建议按职责拆分，避免继续扩大 `usb_link.c`：

```text
main/include/
  usb_link.h
  usb_protocol.h
  usb_session.h
  export_service.h
  record_metadata.h

main/src/usb/
  usb_link.c            # TinyUSB、CDC、MSC 生命周期
  usb_protocol.c        # SLIP、帧解析、命令分发、严格 JSON
  usb_session.c         # 维护会话和跨重启 boot intent

main/src/export/
  export_service.c      # 日期扫描、清单、事务和查询
  export_delete.c       # 路径白名单、幂等删除、断电恢复

main/src/media/
  recorder.c            # READY/START 屏障和采集协调
  record_metadata.c     # schema v2 和原子完成
```

`usb_protocol.c` 不应直接递归删除目录；命令层只能调用 `export_service` 的受限接口。`export_service` 不能调用 TinyUSB。这样才能在不连接真实 USB 的情况下测试事务逻辑。

## 15. 安全和错误处理要求

- 使用 cJSON 或等价的结构化解析器；字段类型、长度、枚举和必填项全部验证。
- 对超过 1600 字节、包含 NUL、非法 UTF-8 或 JSON 尾随数据的请求返回 `BAD_FRAME`/`INVALID_ARG`，不得继续使用部分字段。
- 所有导出 ID、请求 ID和路径字段设置固定最大长度。
- 删除函数只接受经过解析的记录 basename，不接受任意绝对路径。
- 删除时使用 `lstat`/`stat` 校验对象类型，不跟随软链接；FAT 通常没有软链接，但代码仍不应依赖这一假设。
- 看门狗期间分批删除和让出 CPU，避免大目录删除触发任务看门狗。
- 日志不得输出 Wi-Fi 密码、未来可能加入的密钥或完整用户音频内容。
- 每个错误响应包含稳定的机器可读 `reason`，同时保留适合日志查看的 `message`。

## 16. 测试计划

### 16.1 主机侧协议测试

增加 Python 测试夹具，覆盖：

- SLIP 分片、连续帧、转义字节、CRC 错误、长度错误和超大帧。
- 旧版请求字段与 2.0 扩展字段。
- JSON 类型错误、未知字段、非法日期、非法导出 ID。
- 所有新命令的状态和幂等重试。
- 状态 JSON 和清单 JSON schema 校验。

### 16.2 设备侧单元测试

- 日期扫描只返回完整正式记录。
- 临时清单原子替换和损坏文件恢复。
- 路径白名单拒绝 `..`、绝对路径、错格式目录和超长名称。
- `BEGIN_EXPORT` 幂等键重复请求。
- 多日期事务互不干扰。
- `COMMIT` 重复执行。
- 删除到第 N 个目录掉电后的恢复。
- `ABORT` 不删除任何记录。
- RTC boot intent 的 magic、版本、CRC 和枚举校验。

### 16.3 实机测试矩阵

| 测试 | 验收结果 |
| --- | --- |
| 旧桌面端 + 2.0 固件 | HELLO、只读导入、退出 MSC 和旧版清理仍可完成。 |
| 2.0 主机 + 1.x 固件 | 自动回退 `legacy_msc_v1`，不调用新命令。 |
| 2.0 主机 + 2.0 固件 | 使用事务化多日期导出。 |
| 自动唤醒未连接电脑 | 正常录制五秒并深睡。 |
| 手动重启并连接电脑 | 维护握手后不自动录制，保持在线。 |
| MSC 复制中拔线 | 原始记录和 prepared 事务全部保留。 |
| EXIT_MSC 前未弹出 | `force=false` 被拒绝，设备不破坏卷。 |
| 云端或邮件失败 | 不发送 COMMIT，设备记录保留。 |
| COMMIT 前断电 | 记录保留。 |
| COMMIT 每一步断电 | 再次提交可恢复，且不越界删除。 |
| 多日期中一天失败 | 成功日期可提交，失败日期完整保留。 |
| 10000 次序号循环 | 不覆盖、不误删已有目录。 |
| TF 卡满/拔出/损坏 | 返回明确错误，不崩溃，不格式化卡。 |

### 16.4 构建和发布门槛

- `idf.py set-target esp32s3` 后 `idf.py build` 无错误。
- 不新增未解释的编译警告。
- 记录五秒时无任务看门狗、堆栈溢出或明显丢帧回退。
- 维护模式持续 PING 30 分钟稳定。
- 只读 MSC 连续复制大于 2 GB 数据无文件损坏。
- 断电注入测试通过后才能标记 2.0.0 候选版本。
- 发布前保存 `sdkconfig.defaults`、固件 SHA-256、提交号和完整实机测试记录。

## 17. 建议实施里程碑和提交顺序

### 里程碑 0：冻结基线

- 在当前样机运行协议 v1、录制、自动唤醒、深睡和 MSC 冒烟测试。
- 保存串口日志、存储目录样本和内存基线。
- 不修改行为，仅补充可重复测试说明。

提交建议：`test: capture firmware v1 compatibility baseline`

### 里程碑 1：协议与设备身份基础

- 拆分协议解析模块。
- 引入严格 JSON。
- 增加 `firmware_version`、`device_id` 和正式 capabilities。
- 旧命令测试全部保持通过。

提交建议：`feat: add backward-compatible firmware v2 capability layer`

### 里程碑 2：维护会话和重启意图

- 实现带 CRC 的 boot intent。
- 实现 `EXIT_MSC(next_mode=maintenance)` 和 `END_SESSION`。
- 修复退出 MSC 后误进入 Wi-Fi/录制路径的问题。

提交建议：`feat: persist usb maintenance sessions across mode switches`

### 里程碑 3：导出事务和多日期发现

- 实现 `LIST_RECORD_DATES`、`BEGIN_EXPORT`、清单原子写入和查询。
- 支持多个按日期 prepared 事务和一次 MSC 挂载。

提交建议：`feat: add transactional multi-day export manifests`

### 里程碑 4：提交删除和断电恢复

- 实现严格路径校验、`COMMIT_EXPORT_DELETE`、`ABORT_EXPORT` 和幂等状态日志。
- 完成每一步断电注入测试。

提交建议：`feat: add recoverable export commit and precise cleanup`

### 里程碑 5：录制元数据与同步

- 实现临时记录目录、READY/START 屏障、真实偏移和 schema v2。
- 修复 AVI 实际帧间隔、序号碰撞和时间质量。

提交建议：`feat: add atomic recordings and synchronized metadata v2`

### 里程碑 6：资源和体验优化

- 音频/IMU 流式写入。
- LED 状态和事务 housekeeping。
- 长时间稳定性与存储压力测试。

提交建议：`perf: harden recording storage and usb maintenance`

### 里程碑 7：联调与发布文档

- 当前桌面端兼容测试。
- 未来桌面事务适配器夹具测试。
- 编写刷机、回滚、故障恢复和测试报告。

提交建议：`docs: add firmware v2 flashing recovery and validation guide`

每个里程碑必须独立构建、测试、提交并推送。不得把录制格式改造、导出事务和删除事务混为一个无法回滚的大提交。

## 18. 固件 2.0 完成定义

只有同时满足以下条件，才可认为固件 2.0 完成：

1. 旧版桌面应用可以直接连接和同步，不要求同时升级桌面端。
2. 新协议能力可被 HELLO 稳定发现，命令和状态命名与本文一致。
3. 一次连接可以准备并同步多天记录，每天拥有独立事务。
4. 退出 MSC 后设备保持维护，不会中途自动录制或进入 Wi-Fi 门户。
5. 未提交事务在所有失败场景中保留原始数据。
6. 已提交事务只能删除清单内记录，重复提交和断电恢复安全。
7. 新记录具备真实、可解释的 UTC/本地时间、时钟质量、采集偏移和流错误信息。
8. 半成品记录不会出现在正式同步列表。
9. 实机完成自动唤醒、五秒采集、MSC 大文件复制、30 分钟维护和断电注入测试。
10. 分支工作树干净，构建产物、固件摘要、提交号和测试报告可追溯。

## 19. 交给下一段开发对话的任务说明

可将下面内容与本仓库一起直接交给后续开发对话：

> 请在 `D:\\ESP-IDF\\Projects\\Day_Distiller_HW2.0_V1` 的 `codex/firmware-v2` 分支继续实现 Day Distiller 固件 2.0。开始前完整阅读 `docs/firmware_v2_implementation_plan_zh.md`、`docs/usb_link_protocol.md` 和 `docs/usb_link_protocol_v2_draft.md`，并检查当前固件源码。严格保持 USB 帧协议版本 1、命令 1–5、VID/PID、MSC 和现有文件名兼容。按文档里程碑逐步实现：先完成协议/能力和维护会话，再完成多日期导出清单，然后完成可恢复精确删除，最后完成原子记录和元数据 schema v2。每个里程碑必须运行 ESP-IDF 构建和对应测试，独立提交并推送；任何失败都不得删除设备记录。遇到硬件相关问题时先保存串口日志、复现步骤和最小测试结果，不要通过猜测改变引脚或硬件参数。

