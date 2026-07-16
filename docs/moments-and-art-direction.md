# Moments 筛选与 Looki L1 产品研究

## 采用的产品原则

Looki L1把自己定义为“AI life curator”，不是持续录像机。其官方Story Mode说明：设备按间隔记录短片，AI识别微笑、惊喜和有意义的互动，把内容整理成连贯时间线和“One Day in 60 Seconds”。Looki AI页面进一步说明，它结合相机、麦克风和IMU感知光线、声音与动作，把记录组织成主题化Moments，并支持海报等创意输出。

Day Distiller借鉴的是这条信息架构，而不是照搬成品样式：

1. 先把相邻、重复或语义相近的记录理解为同一个Moment，避免把每一帧都当成独立事件。
2. 结合视觉、环境声音、OCR、时间和本地IMU证据，寻找当天的主题与变化。
3. 只选择2–3个最有记忆价值的Moment：有意记录、重要互动、意外变化、个人里程碑、能代表当天主题的小事都优先于单纯“拍得清楚”。
4. 让入选Moment形成一条简短的情绪弧线，再融合为一张无文字海报，而不是生成vlog或多格漫画。
5. 用户可以通过艺术风格预设、自定义提示和单张试片快速纠正审美方向，弥补全自动产品常见的可编辑性不足。

参考资料：

- Looki L1产品页：https://www.looki.ai/products/looki-l1
- Looki Story Mode：https://support.looki.ai/hc/en-001/articles/49659302782233-Story-Mode
- Looki AI：https://support.looki.ai/hc/en-001/articles/49659477121305-Looki-AI
- Serious Insights独立评测：https://www.seriousinsights.net/looki-l1-review/

## 艺术方向边界

无论选择哪种预设或自定义风格，最终请求都统一强制：单张竖版3:4、无文字、无Logo和水印、不虚构额外事件、同一人物跨Moment保持一致、最多3张输入图、输出固定864×1152。风格提示只负责视觉语言，不能覆盖这些事实和成本边界。
