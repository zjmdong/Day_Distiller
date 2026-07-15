# OpenAI API 配置教程

## 你需要准备什么

需要一个可使用 API 的 OpenAI Platform 账户、有效计费方式，以及能访问设置页所填模型的项目。默认模型为逐片理解 `gpt-5.6-terra`、全天综合 `gpt-5.6-sol`、转写 `gpt-4o-transcribe`、生图 `gpt-image-2`；所有模型名都可在应用里修改。

## 创建和保存 API Key

1. 登录 [OpenAI Platform](https://platform.openai.com/)。
2. 进入项目的 API Keys 页面，创建仅供 Day Distiller 使用的新 Key。
3. 复制 Key 后立即回到应用“设置”页，粘贴到“OpenAI API Key”。
4. 点击“保存设置”。应用通过 Python `keyring` 写入 Windows Credential Manager；SQLite 只保存非敏感模型名。
5. 删除剪贴板历史、临时文本或含 Key 的截图。不要把 Key 放入 `.env`、源码、日志、Git 或聊天。

如果组织要求验证才能使用图像模型，请先按 Platform 的组织设置完成验证。模型不可用或无权限时，应用会在 `generating` 阶段失败，并保留设备与本地素材；修改模型或权限后可从报告历史重试。

## 隐私与数据控制

逐片图像理解使用本地抽出的多张关键帧；音频文件交给转写接口；漫画格使用图像生成接口。Responses API 请求显式设置 `store:false`，不会创建持久 Conversation，但 `store:false` 不等同于 Zero Data Retention。正式上线前请根据组织要求阅读 [数据控制说明](https://developers.openai.com/api/docs/guides/your-data)。图像输入和结构化输出实现分别遵循 [图像与视觉](https://developers.openai.com/api/docs/guides/images-vision) 和 [结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs)。

## 第一次真实验收

1. 先用 Mock 完成一整天夹具测试。
2. 在设置页保存 API Key 和 SMTP 配置。
3. 选只有 1–2 条记录的日期，切换到“OpenAI + SMTP”。
4. 确认逐片、生成、排版、邮件均完成，再扩大到全天记录。
5. 遇到 401：Key 错误或项目无权；403：组织/模型权限；429：额度或速率；超时：使用历史页重试。任何这些错误都不会触发设备删除。

## 成本控制

默认每条记录最多发送六张关键帧，每天生成最多六格 Mock 规划、真实综合允许 4–8 格。减少记录数、改用较低图像质量或减少漫画格可降低用量。应用只显示估算，最终账单以 OpenAI Platform 为准。
