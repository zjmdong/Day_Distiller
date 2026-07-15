# 多位置 IMU 数据采集、标注与训练

内置启发式分类器是方向无关的首版基线，会输出 `stationary`、`walking`、`running`、`vehicle_like`、`handling` 或 `unknown`，以及 `surface`、`handheld`、`chest_fixed`、`loose_carried` 或 `unknown`。正式多位置模型必须用真实标注数据训练。

## 采集设计

至少邀请 5–10 名参与者；代码最低只要求 2 人和 20 条记录，但不适合正式评估。每人覆盖不同速度、路面、身材和设备朝向。建议每个“人员 × 位置 × 活动”至少 30 个独立五秒片段。

位置标签固定使用：

- `surface`：设备静置桌面或其他固定表面。
- `handheld`：手持。
- `chest_fixed`：胸前固定。
- `loose_carried`：口袋、包内等松散携带。

活动标签固定使用：`stationary`、`walking`、`running`、`vehicle_like`、`handling`。无法明确判定的片段不要硬标，放入复核集或使用 `unknown`。

## 标签 CSV

UTF-8 CSV 必须包含四列：

```csv
path,participant_id,placement,activity
data/p01_walk_001/imu.json,p01,chest_fixed,walking
data/p02_bus_001/imu.json,p02,loose_carried,vehicle_like
```

`path` 相对 CSV 所在目录解析。`participant_id` 必须匿名且稳定，不要写姓名、手机号或邮箱。每条片段在采集时记录真实起止标签，不能事后只凭视频猜测。

## 训练与人员隔离评测

```powershell
.\.venv\Scripts\Activate.ps1
day-distiller-train-motion .\dataset\labels.csv .\models\motion-v1.joblib
```

脚本抽取与运行时相同的动态加速度、jerk、陀螺能量、姿态变化、频域主峰、周期性、车辆频带比例和频谱熵。`GroupShuffleSplit` 按参与者隔离训练/测试，避免同一个人的片段同时泄漏到两边。终端输出位置与活动准确率和分类报告，模型包内也保存指标。

验收不应只看总准确率：逐类检查 precision/recall、混淆矩阵、不同携带位置和新参与者。对低置信度设置 `unknown` 阈值，并保留启发式回退。部署时在应用“设置 → 可选 IMU 模型 .joblib”填写输出路径；模型文件只应来自可信训练流程，因为 joblib 不适合加载不可信文件。

## 隐私与同意

采集前获取参与者明确同意；说明视频、音频、IMU 的用途和保留期。训练只需要 IMU 时，应尽早去除视频和音频。人员编号与同意记录分开保存，数据集不要提交到公开 Git。
