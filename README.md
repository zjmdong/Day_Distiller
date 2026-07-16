# Day Distiller Windows Client v2

Day Distiller 的 Windows 上位机与“AI 每日蒸馏”工作流。当前版本直接兼容协议 v1 固件，不要求先升级设备。

## 已实现

- 五页 PySide6 应用：设备、今日记录、蒸馏进度、报告历史、设置。
- 现有固件 `HELLO`、`GET_STATUS`、只读/读写 MSC、`PING` 和安全弹出。
- 按日期导入 `REC_XXXX_YYMMDD_HHMMSS`，复制后进行大小与 SHA-256 双重校验。
- SQLite 可恢复任务状态机；失败记录错误与恢复阶段。
- FFprobe/FFmpeg 媒体验证、0.5/2.5/4.5 秒关键帧与场景变化补帧。
- IMU 特征、启发式活动/携带形态分类，以及可加载的 scikit-learn 模型。
- 中国大陆多模型工作流：Qwen 3.5 Omni Plus批量音视频/OCR、Qwen 3.7 Plus关键帧复核、DeepSeek V4 Pro全天筛选与编排、Seedream 5.0 Pro单张无文字竖版海报。
- 确定性 Mock AI 与本地 `.eml` outbox，可在没有 API Key、Resend 或设备时验收。
- 极简同版HTML/PDF、CID内嵌海报、原图与PDF双附件、Resend SMTP、确定性Message-ID、发送回执和邮件成功后的精确设备清理。
- 日报修改、地点记忆、重新生成和手动再次发送。

## 快速开始

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
.\scripts\run.ps1
```

首次验收建议在“蒸馏进度”选择“离线 Mock”，在“今日记录”选择一个虚拟 TF 卡目录。Mock 不发送任何素材到云端，邮件写入 `%LOCALAPPDATA%\DayDistillerV2\mock_outbox`。

真实模式请依次阅读：

- [中国大陆模型配置](docs/mainland-model-setup.md)
- [完整用户指引与 Resend 配置](docs/user-guide.md)
- [漫画参考形象](docs/avatar-setup.md)
- [IMU 数据采集与训练](docs/imu-data-collection.md)
- [USB、TF 卡与 FFmpeg 排障](docs/usb-ffmpeg-troubleshooting.md)

不要在聊天、配置文件、截图或 Git 中提交 API Key 和应用密码；只在应用“设置”页填写。已保存密钥会在本机设置页明文回显，查看页面时注意旁人和录屏软件。

## 当前设备的一键流程

1. 连接设备，应用执行 `HELLO` 和 `GET_STATUS`。
2. 以只读模式进入 MSC，导入所选日期并生成 `import_manifest.json`。
3. 校验本地文件，安全弹出并退出 MSC；生成期间每 30 秒发送一次 `PING`。
4. 完成本地分析、云端生成、HTML/PDF 与邮件发送。
5. 只有 Resend SMTP 服务器明确接受邮件后，才重新以读写模式挂载。
6. 删除前重新核对目录、文件集合、大小和 SHA-256，只删除本次清单中的目录。
7. 清理失败会留下 `pending_cleanup`，可在“报告历史”重试，不影响日报。

## 测试与构建

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\scripts\build.ps1
```

生成的目录版程序位于 `dist\DayDistillerClient`。必须分发整个目录，不能只复制 EXE；FFmpeg、FFprobe和许可证已经内置。

应用数据默认位于 `%LOCALAPPDATA%\DayDistillerV2`，可通过 `DAY_DISTILLER_DATA_DIR` 改到测试目录。原始导入素材与成品长期保留，由用户手动清理。
