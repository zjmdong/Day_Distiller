# 中国大陆模型工作流配置

当前真实云端工作流固定按以下顺序运行：

1. `qwen3.5-omni-plus` 对一条记录的全部候选帧和原始音频执行批量视觉分析、环境声音识别、语音转写和 OCR，并给关键帧评分。
2. `qwen3.7-plus` 复核按评分排序后的关键帧，生成严格的逐片证据，不负责重复转写音频。
3. `deepseek-v4-pro` 接收视觉、音频、OCR、时间戳和本地 IMU 结果，从全天素材中筛选2–3个最有价值、最特殊或最有意义的场景，生成主题标题、暖心话和单张海报编排提示。
4. Seedream 5.0 Pro 接收最多3张入选场景的原始关键帧，只调用一次，生成一张无文字竖版纪念海报。

## 准备凭据

- 在阿里云百炼北京地域创建 API Key，并确保工作空间可调用 `qwen3.7-plus` 和 `qwen3.5-omni-plus`。
- 在 DeepSeek 开放平台创建 API Key，并确认 `deepseek-v4-pro` 权限。
- 在火山方舟创建推理 API Key，开通 Seedream 5.0 Pro，并把控制台实际显示的模型或 Endpoint ID 填入应用。
- 凭据只能填入应用设置页。应用通过系统安全凭据库（Windows Credential Manager / macOS Keychain）保存，不写入配置文件或日志；为便于核对，保存后会在本机设置页明文回显。

公开文档中的 Seedream 名称可能与账号控制台中的 Endpoint ID 不同。设置页模型字段以控制台实际值为准。
百炼新工作空间通常使用 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` 形式的地址；请从控制台复制实际地址并替换 `{WorkspaceId}`，不要把大括号占位符原样填入设置页。

## 调用参数

- Qwen 3.5 Omni Plus按官方兼容接口要求使用流式输出；本地音频以 `data:;base64,...` 数据URI发送，避免被服务端误判成无效URL。百炼模型关闭思考，专注忠实提取视觉、环境声、转写和OCR证据。
- DeepSeek V4 Pro启用思考模式，并使用 `high` 推理等级完成全天证据冲突消解和编排。
- Seedream固定输出 `864x1152` 的1K级竖版海报，共995,328像素，显著低于220万像素硬上限。设置页将该值设为只读，避免误触更高计费档位。
- 海报请求最多输入3张图片并关闭水印，不发送仅旧模型支持的组图和流式参数。优先输入2–3张入选Moment原始帧；只有两个Moment时，用户参考形象才会占用剩余的第三个位置。

兼容接口偶尔会在合法JSON末尾附加Markdown围栏，或把数组、评分字段输出成近似类型。应用会提取首个完整JSON对象、规范常见类型漂移，再执行严格字段校验；无效或缺失的核心日报内容仍会使任务失败。任务在生图、排版或邮件阶段失败后，会从已有证据或报告继续，不会重新调用已经完成的上游模型。

官方参考：[百炼 Qwen Omni](https://help.aliyun.com/zh/model-studio/qwen-omni)、[DeepSeek思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[火山方舟图像生成](https://www.volcengine.com/docs/82379/1541523?lang=zh)、[Resend SMTP](https://resend.com/docs/send-with-smtp)。

## 数据与本地处理边界

- 视频只在本地用 FFmpeg 抽帧，候选关键帧和对应音频会发送到百炼。
- IMU 原始采样和特征计算保持本地；只把分类结果、置信度和必要特征摘要交给 DeepSeek。
- 地点记忆只保存在本地 SQLite，使用用户纠正和图片签名，不调用向量模型。
- 只有结构化场景证据发送给 DeepSeek，不发送原始视频或音频。

## 首次验收

先使用“离线 Mock”完成端到端测试，再选择记录较少的日期测试真实模型。依次核对：Omni转写和环境声音、OCR、Qwen关键帧结论、DeepSeek筛选的2–3个场景、单张Seedream海报、PDF以及邮件。任一云端步骤失败都不会删除设备记录。
