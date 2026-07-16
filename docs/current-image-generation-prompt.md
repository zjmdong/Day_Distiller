# 当前最终图片提示词

## 当前已选艺术风格：当代漫画

```text
Premium contemporary graphic-novel illustration with expressive ink contours, cinematic perspective, controlled halftone texture, layered cel shading and rich but harmonious colors. Fuse the selected moments into one continuous visual narrative, without comic boxes, gutters, speech balloons, captions, or printed sound effects.
```

## Seedream 最终请求模板

应用会把上面的艺术风格提示词填入 `{art_style_prompt}`，并把 DeepSeek 当日编排得到的场景描述填入 `{daily_image_prompt}`：

```text
Create ONE finished vertical 3:4 daily-memory poster, not separate outputs. Selectively fuse the referenced real moments into one cohesive editorial composition with a clear visual hierarchy and seamless transitions between two or three scenes. The selected art direction is '{art_style_name}'. Follow this art direction precisely: {art_style_prompt} Preserve the recognizable actions, environment and personal details supported by the references, while artistically simplifying them. When the same protagonist appears in multiple references, keep their visual identity consistent and make any repeated depiction read clearly as a montage across moments, not as invented extra people. Do not invent extra events. The poster must be pure image: absolutely no text, letters, numbers, captions, subtitles, speech bubbles, logos, watermark, frames with written labels, or legible signage. {daily_image_prompt}{reference_note}
```

`{reference_note}` 会说明输入了几张事实参考图；如果设置了参考形象文字描述，也会附加非生物识别的角色风格说明。
